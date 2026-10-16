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
})();
</script>'''

    def home_with_management():
        html = Path(base.__file__).with_name('shared.html').read_text(encoding='utf-8')
        return html.replace('</body>', enhancement + '</body>')

    app.view_functions['home'] = home_with_management
