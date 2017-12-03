import os
import rethinkdb as db
import requests
import json

from datetime import datetime, timezone
from functools import wraps
from requests_oauthlib import OAuth2Session
from flask import Flask, render_template, url_for, redirect, g, request, session, send_from_directory, abort, jsonify
from flask_wtf.csrf import CSRFProtect

ADMINS = os.environ.get("ADMINS").split(',')

# RETHINKDB
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST")
RETHINKDB_DB = os.environ.get("RETHINKDB_DB")
RETHINKDB_USER = os.environ.get("RETHINKDB_USER")
RETHINKDB_PASSWORD = os.environ.get("RETHINKDB_PASSWORD")

# REDDIT API
REDDIT_APP_ID = os.environ.get("REDDIT_APP_ID")
REDDIT_APP_SECRET = os.environ.get("REDDIT_APP_SECRET")
REDDIT_REDIRECT_URI = os.environ.get("REDDIT_REDIRECT_URI")

REDDIT_API_BASE_URL = "https://www.reddit.com/api/v1"
REDDIT_OAUTH_BASE_URL = "https://oauth.reddit.com/api/v1"

app = Flask(__name__)
csrf = CSRFProtect(app)

app.debug = True

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'this_should_be_configured')

# open connection before each request
@app.before_request
def before_request():
    session.permanent = True

    try:
        g.db_conn = db.connect(host=RETHINKDB_HOST, port=28015, db=RETHINKDB_DB, user=RETHINKDB_USER, password=RETHINKDB_PASSWORD).repl()

    except db.errors.ReqlDriverError:
        return render_template('error.html', session=session,  error={
            'message': 'o fucc this should never happen you should tell someone <br><br> ReqlDriverError'
        })

# close the connection after each request
@app.teardown_request
def teardown_request(exception):
    try:
        g.db_conn.close()
    except AttributeError:
        pass

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = session.get('user', None)

        if not user:
            return redirect(url_for('login_reddit'))

        if user['id'] not in ADMINS:
            return 'ur not admin lmao'

        return f(*args, **kwargs)
    return wrapper

@app.route('/', methods=['GET', 'POST'])
def home():
    now = datetime.utcnow()
    voting_start = datetime(now.year if now.month % 12 else now.year + 1, now.month % 12 + 1, 1, tzinfo=timezone.utc).timestamp()
    voting_end = datetime(now.year, now.month, 8, tzinfo=timezone.utc).timestamp()

    if now.timestamp() <= voting_start and now.timestamp() < voting_end:
        if session.get('user', None) and request.method == 'POST' and request.form.get('vote'):
            vote = request.form.get('vote')
            query = db.table('votes').filter({'user_id': session.get('user')['id']})

            if(query.count().run()): query.update({'vote': vote}).run()
            else: db.table('votes').insert([{'user_id': session.get('user')['id'], 'vote': vote}]).run()

        current_vote = list(db.table('votes').filter({'user_id': session.get('user')['id']}).run()) if session.get('user') else None
        return render_template('voting.html',
                                session=session,
                                admins=ADMINS,
                                countdown_to=voting_end,
                                submissions=db.table('submissions').order_by('title').run(),
                                current_vote=current_vote[0]['vote'] if current_vote else None
                            )

    else:
        votes = list(db.table('votes').run())
        submissions = []

        for submission in db.table('submissions').order_by('title').run():
            submission['score'] = sum([1 for vote in votes if vote['vote'] == submission['id']])
            submissions.append(submission)

        return render_template('winner.html',
                        session=session,
                        admins=ADMINS,
                        countdown_to=voting_start,
                        winner=sorted(submissions, key=lambda k: k['score'], reverse=True)[0]
                    )

@app.route('/login')
def login_reddit():
    # Check for state and for 0 errors
    state = session.get('oauth2_state')
    if request.values.get('error'):
        return render_template('error.html', session=session,  error= {
            'message': 'There was an error authenticating with reddit: {}'.format(request.values.get('error')),
            'link': '<a href="{}">Return Home</a>'.format(url_for('verify'))
        })

    if state and request.args.get('code'):
        # Fetch token
        client_auth = requests.auth.HTTPBasicAuth(REDDIT_APP_ID, REDDIT_APP_SECRET)
        post_data = {"grant_type": "authorization_code", "code": request.args.get('code'), "redirect_uri": REDDIT_REDIRECT_URI}
        reddit_token = requests.post(REDDIT_API_BASE_URL + "/access_token", auth=client_auth, data=post_data, headers={'User-agent': 'low_poly web auth, /u/RenegadeAI'}).json()

        if not reddit_token or not 'access_token' in reddit_token:
            return redirect(url_for('logout'))

        # Fetch the user
        user = requests.get(REDDIT_OAUTH_BASE_URL + "/me", headers={"Authorization": "bearer {}".format(reddit_token["access_token"]), 'User-agent': 'low_poly web, /u/RenegadeAI'}).json()

        session['user'] = {key: user[key] for key in ['name', 'id']}

        return redirect(url_for('home'))

    else:
        scope = ['identity']
        reddit = make_reddit_session(scope=scope)
        authorization_url, state = reddit.authorization_url(
            REDDIT_API_BASE_URL + "/authorize",
            access_type="offline"
        )
        session['oauth2_state'] = state
        return redirect(authorization_url)

@app.route('/admin', methods=['GET', 'POST'])
@require_auth
def admin():
    action = request.form.get('action', None)
    _id = request.form.get('id', None)
    if request.method == 'POST' and action and _id:
        if action == 'edit':
            title = request.form.get('title', None)
            image = request.form.get('image', None)

            if title and image:
                db.table('submissions').get(_id).update({'title': title, 'image': image}).run()

            else: return 'Missing required arguments'

        elif action == 'remove':
            db.table('submissions').get(_id).delete().run()

        else: return 'Undefined action'

    votes = list(db.table('votes').run())
    submissions = []

    for submission in db.table('submissions').order_by('title').run():
        submission['score'] = sum([1 for vote in votes if vote['vote'] == submission['id']])
        submissions.append(submission)

    return render_template('admin.html',
                            session=session,
                            submissions=sorted(submissions, key=lambda k: k['score'], reverse=True)
                        )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

def to_json(value):
    return json.dumps(value)

app.jinja_env.filters['to_json'] = to_json

def make_reddit_session(token=None, state=None, scope=None):
    return OAuth2Session(
        client_id=REDDIT_APP_ID,
        token=token,
        state=state,
        scope=scope,
        redirect_uri=REDDIT_REDIRECT_URI,
        auto_refresh_kwargs={
            'client_id':None,
            'client_secret':None,
        },
        auto_refresh_url=None,
        token_updater=None
    )