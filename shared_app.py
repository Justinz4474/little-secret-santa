import os, json, secrets, hashlib, time, threading
from collections import defaultdict, deque
from contextlib import contextmanager
import psycopg
from flask import Flask, request, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config['MAX_CONTENT_LENGTH'] = 16384
ready = False
init_lock = threading.Lock()
rate_lock = threading.Lock()
attempts = defaultdict(deque)

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

@app.errorhandler(Problem)
def problem(e):
    return jsonify(error=e.message), e.status

@app.errorhandler(413)
def too_large(e):
    return jsonify(error='Request too large.'), 413

@app.errorhandler(psycopg.Error)
def db_error(e):
    app.logger.error('Database request failed: %s', type(e).__name__)
    return jsonify(error='Database connection unavailable. Please try again shortly.'), 503

@app.after_request
def headers(r):
    r.headers['Cache-Control'] = 'no-store'
    r.headers['X-Content-Type-Options'] = 'nosniff'
    r.headers['Referrer-Policy'] = 'no-referrer'
    r.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return r

@contextmanager
def database():
    global ready
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise Problem('The shared app still needs its DATABASE_URL setting in Render.', 503)
    with psycopg.connect(url, connect_timeout=10) as con:
        if not ready:
            with init_lock:
                if not ready:
                    with con.cursor() as cur:
                        cur.execute('SELECT pg_advisory_xact_lock(739284106)')
                        cur.execute('CREATE SCHEMA IF NOT EXISTS secret_santa_v1')
                        cur.execute('CREATE TABLE IF NOT EXISTS secret_santa_v1.exchanges (id text PRIMARY KEY, data jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())')
                    con.commit()
                    ready = True
        yield con


def data():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise Problem('Please send a valid form.')
    return value


def string(d, key, limit, required=False):
    value = d.get(key, '')
    if not isinstance(value, str):
        raise Problem('Invalid '+key+'.')
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise Problem('Please check '+key+'.')
    return value


def credentials(d):
    name = string(d, 'name', 60, True)
    password = d.get('password', '')
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise Problem('Use a password with 8–128 characters.')
    return name, password


def throttle(room='create'):
    key = (request.remote_addr, room)
    now = time.monotonic()
    with rate_lock:
        if len(attempts) > 10000:
            for k in list(attempts):
                if not attempts[k] or attempts[k][-1] < now-900:
                    del attempts[k]
            if len(attempts) > 10000:
                raise Problem('Please try again later.', 429)
        q = attempts[key]
        while q and q[0] < now-900:
            q.popleft()
        if len(q) >= 20:
            raise Problem('Too many attempts. Wait 15 minutes and try again.', 429)
        q.append(now)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_member(name, password):
    token = secrets.token_urlsafe(32)
    return {'id':secrets.token_hex(16), 'name':name, 'password':generate_password_hash(password), 'token':digest(token), 'expires':time.time()+2592000, 'wishes':[]}, token


def authenticate(room):
    token = request.headers.get('Authorization', '')
    if not token.startswith('Bearer ') or len(token) > 150:
        raise Problem('Please sign in to this exchange.', 401)
    hashed = digest(token[7:])
    for member in room['members']:
        if secrets.compare_digest(member['token'], hashed) and member['expires'] > time.time():
            return member
    raise Problem('Your session expired. Please sign in again.', 401)


def view(room, member):
    result = {'title':room['title'], 'budget':room['budget'], 'date':room['date'], 'drawn':room['drawn'], 'host':member['id']==room['host'], 'name':member['name'], 'wishes':member['wishes'], 'members':[{'name':m['name'],'count':len(m['wishes'])} for m in room['members']]}
    if room['drawn']:
        member_ids = {m['id'] for m in room['members']}
        pairs = room.get('pairs', {})
        for participant, display in zip(room['members'], result['members']):
            givers = [giver for giver, recipient in pairs.items() if recipient == participant['id']]
            assigned = len(givers) == 1 and givers[0] in member_ids and givers[0] != participant['id']
            display['name'] += ' — ' + ('Exactly one gift-giver assigned (saved draw)' if assigned else 'Gift-giver assignment needs checking')
        match = next(m for m in room['members'] if m['id']==room['pairs'][member['id']])
        result['match'] = {'name':match['name'], 'wishes':match['wishes']}
    return result


def persist(cur, rid, room):
    cur.execute('UPDATE secret_santa_v1.exchanges SET data=%s::jsonb WHERE id=%s', (json.dumps(room), rid))

@app.get('/')
def home():
    return send_file('shared.html')

@app.get('/health')
def health():
    return jsonify(ok=True, database_configured=bool(os.environ.get('DATABASE_URL')))

@app.post('/api/create')
def create():
    throttle()
    d = data()
    name, password = credentials(d)
    title = string(d,'title',100,True)
    member, token = new_member(name,password)
    rid = secrets.token_urlsafe(24)
    room = {'title':title, 'budget':string(d,'budget',40), 'date':string(d,'date',20), 'host':member['id'], 'members':[member], 'drawn':False, 'pairs':{}}
    with database() as con, con.cursor() as cur:
        cur.execute('INSERT INTO secret_santa_v1.exchanges(id,data) VALUES(%s,%s::jsonb)', (rid,json.dumps(room)))
    return jsonify(room=rid,token=token,state=view(room,member)),201

@app.route('/api/room/<rid>', methods=['GET','POST'])
def exchange(rid):
    if not 20 <= len(rid) <= 80:
        raise Problem('Exchange not found.',404)
    d = data() if request.method=='POST' else {}
    action = d.get('action')
    if action in ('join','login'):
        throttle(rid)
    with database() as con, con.cursor() as cur:
        cur.execute('SELECT data FROM secret_santa_v1.exchanges WHERE id=%s FOR UPDATE', (rid,))
        row = cur.fetchone()
        if not row:
            raise Problem('Exchange not found. Check the invitation link.',404)
        room = row[0]
        token = None
        if action in ('join','login'):
            name, password = credentials(d)
            existing = next((m for m in room['members'] if m['name'].casefold()==name.casefold()),None)
            if action=='join':
                if room['drawn']:
                    raise Problem('The names are already drawn. New people cannot join.')
                if existing:
                    raise Problem('That name is taken. Sign in, or use a last initial.')
                if len(room['members'])>=30:
                    raise Problem('This test supports up to 30 people per exchange.')
                member,token = new_member(name,password)
                room['members'].append(member)
            else:
                if not existing or not check_password_hash(existing['password'],password):
                    raise Problem('Name or password is incorrect.',401)
                member = existing
                token = secrets.token_urlsafe(32)
                member['token'],member['expires'] = digest(token),time.time()+2592000
            persist(cur,rid,room)
        else:
            member = authenticate(room)
            if request.method=='POST':
                if action=='wishes':
                    wishes=d.get('wishes')
                    if not isinstance(wishes,list) or not 3<=len(wishes)<=5 or any(not isinstance(w,str) or not w.strip() or len(w)>300 for w in wishes):
                        raise Problem('Enter 3–5 gift ideas, each up to 300 characters.')
                    member['wishes']=[w.strip() for w in wishes]
                elif action=='draw':
                    if member['id']!=room['host']:
                        raise Problem('Only the organizer can draw names.',403)
                    if room['drawn']:
                        raise Problem('Names have already been drawn. Matches cannot be changed.')
                    people=room['members']
                    if len(people)<3 or any(len(m['wishes'])<3 for m in people):
                        raise Problem('At least 3 people must join, each with 3–5 gift ideas.')
                    ids=[m['id'] for m in people]
                    matched=ids.copy()
                    while True:
                        secrets.SystemRandom().shuffle(matched)
                        if all(a!=b for a,b in zip(ids,matched)):
                            break
                    room['pairs']=dict(zip(ids,matched))
                    room['drawn']=True
                elif action=='logout':
                    member['token']=digest(secrets.token_urlsafe(32))
                else:
                    raise Problem('Unknown action.')
                persist(cur,rid,room)
        result={'state':view(room,member)}
        if token:
            result['token']=token
        return jsonify(result)
