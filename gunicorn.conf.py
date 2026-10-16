def post_worker_init(worker):
    import shared_app as base
    from pathlib import Path
    app = base.app
    if app is not worker.wsgi:
        raise RuntimeError('Unexpected Secret Santa application')

    def remove_participant(rid):
        if not 20 <= len(rid) <= 80:
            raise base.Problem('Exchange not found.', 404)
        d = base.data()
        name = base.string(d, 'name', 60, True)
        with base.database() as con, con.cursor() as cur:
            cur.execute('SELECT data FROM secret_santa_v1.exchanges WHERE id=%s FOR UPDATE', (rid,))
            row = cur.fetchone()
            if not row:
                raise base.Problem('Exchange not found.', 404)
            room = row[0]
            member = base.authenticate(room)
            if member['id'] != room['host']:
                raise base.Problem('Only the organizer can remove people.', 403)
            if room['drawn']:
                raise base.Problem('People cannot be removed after names are drawn.')
            target = next((m for m in room['members'] if m['name'].casefold() == name.casefold()), None)
            if not target:
                raise base.Problem('This person is no longer in the exchange.', 404)
            if target['id'] == room['host']:
                raise base.Problem('The organizer cannot remove themselves.')
            room['members'] = [m for m in room['members'] if m['id'] != target['id']]
            base.persist(cur, rid, room)
            return base.jsonify(state=base.view(room, member))

    app.add_url_rule('/api/remove/<rid>', endpoint='remove_participant', view_func=remove_participant, methods=['POST'])

    def update_budget(rid):
        if not 20 <= len(rid) <= 80:
            raise base.Problem('Exchange not found.', 404)
        d = base.data()
        budget = base.string(d, 'budget', 40)
        with base.database() as con, con.cursor() as cur:
            cur.execute('SELECT data FROM secret_santa_v1.exchanges WHERE id=%s FOR UPDATE', (rid,))
            row = cur.fetchone()
            if not row:
                raise base.Problem('Exchange not found.', 404)
            room = row[0]
            member = base.authenticate(room)
            if member['id'] != room['host']:
                raise base.Problem('Only the organizer can edit the budget.', 403)
            room['budget'] = budget
            base.persist(cur, rid, room)
            return base.jsonify(state=base.view(room, member))

    app.add_url_rule('/api/budget/<rid>', endpoint='update_budget', view_func=update_budget, methods=['POST'])

    enhancement = r'''<script>
(function(){
function addRemoveButtons(){
    if(!state||!state.host||state.drawn)return;
    for(const li of $('members').children){
        if(li.querySelector('button'))continue;
        const name=li.firstChild.textContent;
        if(name===state.name)continue;
        const button=document.createElement('button');
        button.type='button';button.className='soft';button.textContent='Remove';
        button.style.cssText='margin:0 0 0 12px;padding:6px 10px;font-size:12px';
        button.setAttribute('aria-label','Remove '+name+' from this exchange');
        button.onclick=()=>{
            if(busy||!confirm('Remove '+name+' and their wishlist from this exchange? They can rejoin using the invitation link before the draw.'))return;
            task(async()=>{
                const r=await api('/api/remove/'+encodeURIComponent(room),{name});
                render(r.state);message(name+' was removed from this exchange.');
            });
        };
        li.append(button);
    }
}
new MutationObserver(addRemoveButtons).observe($('members'),{childList:true,subtree:true});
addRemoveButtons();

let budgetDirty=false;
function budgetControls(next){
    let form=$('budgetEditForm');
    if(!form){
        form=document.createElement('form');form.id='budgetEditForm';form.className='hidden';
        const label=document.createElement('label');label.htmlFor='editBudget';label.textContent='Edit budget (organizer only)';
        const input=document.createElement('input');input.id='editBudget';input.maxLength=40;input.placeholder='e.g. $25 USD';
        input.oninput=()=>budgetDirty=true;
        const note=document.createElement('p');note.className='sub';note.textContent='Include the currency. Changes apply to everyone and do not change matches.';
        const save=document.createElement('button');save.type='submit';save.className='soft';save.textContent='Save budget';
        form.append(label,input,note,save);$('roomDetails').after(form);
        form.onsubmit=e=>{
            e.preventDefault();if(busy||!state?.host)return;
            const budget=$('editBudget').value.trim();
            if(state.drawn&&!confirm('Update the budget for everyone? Names are already drawn; matches will stay the same.'))return;
            task(async()=>{
                const r=await api('/api/budget/'+encodeURIComponent(room),{budget});
                budgetDirty=false;render(r.state);message('Budget updated for everyone.');
            });
        };
    }
    form.classList.toggle('hidden',!next.host);
    if(!budgetDirty)$('editBudget').value=next.budget||'';
}
const previousRender=render;
render=function(next){previousRender(next);budgetControls(next);};
if(state)budgetControls(state);

})();
</script>'''

    def home_with_management():
        html = Path(base.__file__).with_name('shared.html').read_text(encoding='utf-8')
        return html.replace('</body>', enhancement + '</body>')

    app.view_functions['home'] = home_with_management
