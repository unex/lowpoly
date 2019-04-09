import os, sys
import requests
import json
import logging
import pymongo
from pymongo import MongoClient
from bson.objectid import ObjectId

from datetime import datetime, timezone
from functools import wraps
from requests_oauthlib import OAuth2Session
from flask import Flask, render_template, url_for, redirect, g, request, session, send_from_directory, abort, jsonify
from flask_wtf.csrf import CSRFProtect

ADMINS = os.environ.get("ADMINS").split(',')

# DATABASE
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_DB = os.environ.get("DB_DB")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")

# REDDIT API
REDDIT_APP_ID = os.environ.get("REDDIT_APP_ID")
REDDIT_APP_SECRET = os.environ.get("REDDIT_APP_SECRET")
REDDIT_REDIRECT_URI = os.environ.get("REDDIT_REDIRECT_URI")

REDDIT_API_BASE_URL = "https://www.reddit.com/api/v1"
REDDIT_OAUTH_BASE_URL = "https://oauth.reddit.com/api/v1"

app = Flask(__name__)
csrf = CSRFProtect(app)

app.logger.addHandler(logging.StreamHandler(sys.stdout))
app.logger.setLevel(logging.DEBUG)
# app.logger.debug('debug')

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'this_should_be_configured')

mongo = MongoClient(host=DB_HOST, port=int(DB_PORT), username=DB_USER, password=DB_PASSWORD, authSource=DB_DB, authMechanism='SCRAM-SHA-256')
db = mongo[DB_DB]

@app.before_request
def before_request():
    if request.url.startswith('http://') and not app.debug:
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

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

            db.votes.update_one(
                {"id": session.get("user")["id"]},
                {"$set": {"vote": vote}},
                upsert = True
            )

        current_vote = db.votes.find_one({"id": session.get("user")["id"]}) if session.get("user") else None
        return render_template('voting.html',
                                session=session,
                                admins=ADMINS,
                                countdown_to=voting_end,
                                submissions=db.submissions.find({"$query": {}, "$orderby": {"title": pymongo.ASCENDING}}),
                                current_vote=ObjectId(current_vote["vote"]) if current_vote else None
                            )

    else:
        return render_template('winner.html',
                        session=session,
                        admins=ADMINS,
                        countdown_to=voting_start,
                        winner=db.submissions.find_one({"_id": ObjectId(count_votes()[0]["_id"])})
                    )

@app.route('/login')
def login_reddit():
    # Check for state and for 0 errors
    state = session.get('oauth2_state')
    if request.values.get('error'):
        return "\r\n".join([
            f'<pre>'
            f'There was an error authenticating with reddit: {request.values.get("error")}',
            f'<a href="{url_for("logout")}">Return Home</a>'
            f'</pre>'
        ])

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
    _id = request.form.get('_id', None)
    if request.method == 'POST' and action and _id:
        if action == 'edit':
            title = request.form.get('title', None)
            image = request.form.get('image', None)

            if title and image:
                db.submissions.find_one_and_update(
                    {"_id": ObjectId(_id)},
                    {"$set": {'title': title, 'image': image}}
                )

            else: return 'Missing required arguments'

        elif action == 'remove':
            db.submissions.delete_one({"_id": ObjectId(_id)})

        else: return 'Undefined action'

    votes = {vote["_id"]: vote["count"] for vote in count_votes()}

    submissions = []
    for submission in db.submissions.find({"$query": {}, "$orderby": {"title": pymongo.ASCENDING}}):
        submission["_id"] = str(submission["_id"])
        submission["score"] = votes[submission["_id"]] if submission["_id"] in votes else 0
        submissions.append(submission)

    return render_template('admin.html',
                            session=session,
                            submissions=sorted(submissions, key=lambda k: k['score'], reverse=True)
                        )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

def count_votes():
    _votes = db.votes.aggregate([
        {
            "$group": {
                "_id": "$vote",
                "count": {"$sum": 1}
            }
        },
        {"$sort": { "count": -1}}
    ])

    return list(_votes)

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

if __name__ == '__main__':
    app.run(debug=True)
