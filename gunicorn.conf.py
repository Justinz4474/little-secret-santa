def post_worker_init(worker):
    import shared_app as base
    from pathlib import Path
    app = base.app
    if app is not worker.wsgi:
        raise RuntimeError('Unexpected Secret Santa application')
    original_view = base.view

    def view_with_rules(room, member):
        result = original_view(room, member)
        result['rules'] = room.get('rules', '')
        result['drawVersion'] = room.get('draw_version', 0)
        return result
    base.view = view_with_rules

    def manage(rid, action):
        if not 20 <= len(rid) <= 80:
            raise base.Problem('Exchange not found.', 404)
        d = base.data()
        field = 'name' if action == 'remove' else action
        value = '' if action == 'reset' else base.string(d, field, 4000 if action == 'rules' else 60 if action == 'remove' else 40, action == 'remove')
        with base.database() as con, con.cursor() as cur:
            cur.execute('SELECT data FROM secret_santa_v1.exchanges WHERE id=%s FOR UPDATE', (rid,))
            row = cur.fetchone()
            if not row:
                raise base.Problem('Exchange not found.', 404)
            room = row[0]
            member = base.authenticate(room)
            if member['id'] != room['host']:
                raise base.Problem('Only the organizer can manage this exchange.', 403)
            if action == 'reset':
                if not room['drawn']:
                    raise base.Problem('There is no active draw to reset.')
                version = d.get('version')
                if type(version) is not int or version != room.get('draw_version', 0):
                    raise base.Problem('The draw has changed. Refresh the group before resetting.', 409)
                room['pairs'] = {}
                room['drawn'] = False
                room['draw_version'] = version + 1
            elif action == 'remove':
                if room['drawn']:
                    raise base.Problem('People cannot be removed after names are drawn.')
                target = next((m for m in room['members'] if m['name'].casefold() == value.casefold()), None)
                if not target:
                    raise base.Problem('This person is no longer in the exchange.', 404)
                if target['id'] == room['host']:
                    raise base.Problem('The organizer cannot remove themselves.')
                room['members'] = [m for m in room['members'] if m['id'] != target['id']]
            else:
                room[action] = value
            base.persist(cur, rid, room)
            return base.jsonify(state=base.view(room, member))

    app.add_url_rule('/api/remove/<rid>', 'remove_participant', lambda rid: manage(rid, 'remove'), methods=['POST'])
    app.add_url_rule('/api/budget/<rid>', 'update_budget', lambda rid: manage(rid, 'budget'), methods=['POST'])
    app.add_url_rule('/api/rules/<rid>', 'update_rules', lambda rid: manage(rid, 'rules'), methods=['POST'])
    app.add_url_rule('/api/reset/<rid>', 'reset_draw', lambda rid: manage(rid, 'reset'), methods=['POST'])
    enhancement = r'''<script>
(function(){
function addRemoveButtons(){
    if(!state||!state.host||state.drawn)return;
    for(const li of $('members').children){
        if(li.querySelector('button'))continue;
        const name=li.firstChild.textContent;if(name===state.name)continue;
        const button=document.createElement('button');button.type='button';button.className='soft';button.textContent='Remove';
        button.style.cssText='margin:0 0 0 12px;padding:6px 10px;font-size:12px';
        button.setAttribute('aria-label','Remove '+name+' from this exchange');
        button.onclick=()=>{
            if(busy||!confirm('Remove '+name+' and their wishlist? They can rejoin using the invitation link before the draw.'))return;
            task(async()=>{const r=await api('/api/remove/'+encodeURIComponent(room),{name});render(r.state);message(name+' was removed from this exchange.');});
        };li.append(button);
    }
}
new MutationObserver(addRemoveButtons).observe($('members'),{childList:true,subtree:true});addRemoveButtons();
const edited={budget:false,rules:false};
function makeEditor(field,section,labelText,maxLength){
    const form=document.createElement('form');form.id=field+'EditForm';
    const label=document.createElement('label');label.htmlFor='edit_'+field;label.textContent=labelText+' (organizer only)';
    const input=document.createElement(field==='rules'?'textarea':'input');input.id='edit_'+field;input.maxLength=maxLength;
    if(field==='rules'){
        input.rows=6;input.placeholder='Add your group rules here, one per line.';
        input.style.cssText='width:100%;padding:12px;border:1px solid #dedfd3;border-radius:9px;font:inherit;color:#183f36;resize:vertical';
    }else input.placeholder='e.g. $25 USD';
    input.oninput=()=>edited[field]=true;
    const note=document.createElement('p');note.className='sub';note.textContent=field==='rules'?'Everyone in this exchange can read these rules. Up to 4,000 characters.':'Include the currency. Changes apply to everyone without changing matches.';
    const save=document.createElement('button');save.type='submit';save.className='soft';save.textContent='Save '+field;
    form.append(label,input,note,save);section.append(form);
    form.onsubmit=e=>{
        e.preventDefault();if(busy||!state?.host)return;
        if(field==='budget'&&state.drawn&&!confirm('Update the budget for everyone? Matches will stay the same.'))return;
        const value=input.value.trim();
        task(async()=>{const r=await api('/api/'+field+'/'+encodeURIComponent(room),{[field]:value});edited[field]=false;render(r.state);message(field==='rules'?'Rules saved for everyone in the exchange.':'Budget updated for everyone.');});
    };
}
function controls(next){
    if(!$('resetDraw')){
        const button=document.createElement('button');button.id='resetDraw';button.type='button';button.className='soft hidden';button.textContent='Reset draw';
        $('refresh').after(button);
        button.onclick=()=>{
            if(busy||!state?.host||!state.drawn)return;
            if(!confirm('Clear all current matches and reopen joining? Everyone keeps their name, account, and wishlist. Budget and rules stay saved. People may remember their old match. You can draw again afterward.'))return;
            const version=state.drawVersion;
            task(async()=>{const r=await api('/api/reset/'+encodeURIComponent(room),{version});hideMatch();render(r.state);message('Draw reset. Everyone and their wishlists are saved. The organizer can draw again when ready.');$('roomTitle').scrollIntoView({behavior:'smooth'});});
        };
    }
    $('resetDraw').classList.toggle('hidden',!next.host||!next.drawn);
    if(!$('budgetEditForm')){
        const container=document.createElement('div');$('roomDetails').after(container);
        makeEditor('budget',container,'Edit budget',40);
    }
    if(!$('rulesSection')){
        const section=document.createElement('section');section.id='rulesSection';section.className='card';
        const title=document.createElement('h2');title.textContent='Exchange rules';
        const content=document.createElement('p');content.id='rulesContent';content.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7';
        section.append(title,content);makeEditor('rules',section,'Edit rules',4000);
        $('wishForm').closest('.card').before(section);
    }
    $('rulesContent').textContent=next.rules||'The organizer has not added any rules yet.';
    for(const field of ['budget','rules']){
        $(field+'EditForm').classList.toggle('hidden',!next.host);
        if(!edited[field])$('edit_'+field).value=next[field]||'';
    }
}
const previousRender=render;render=function(next){if(!next.drawn||(state&&state.drawVersion!==next.drawVersion))hideMatch();previousRender(next);controls(next);};if(state)controls(state);
})();
</script>'''

    def home_with_management():
        html = Path(base.__file__).with_name('shared.html').read_text(encoding='utf-8')
        return html.replace('</body>', enhancement + '</body>')
    app.view_functions['home'] = home_with_management
