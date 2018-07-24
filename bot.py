import os
import re
import praw
import rethinkdb as db
import calendar
import requests

from io import BytesIO
from PIL import Image
from datetime import datetime
from colorcube import ColorCube

class objdict(dict):
    def __getattr__(self, name):
        if name in self:
            return self[name]
        else:
            raise AttributeError("No such attribute: " + name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        if name in self:
            del self[name]
        else:
            raise AttributeError("No such attribute: " + name)

# RETHINKDB
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST")
RETHINKDB_DB = os.environ.get("RETHINKDB_DB")
RETHINKDB_USER = os.environ.get("RETHINKDB_USER")
RETHINKDB_PASSWORD = os.environ.get("RETHINKDB_PASSWORD")

# REDDIT
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")
REDDIT_CLIENT_REFRESH_TOKEN = os.environ.get("REDDIT_CLIENT_REFRESH_TOKEN")

# IMGUR
IMGUR_CLIENT_ID = os.environ.get("IMGUR_CLIENT_ID")

# SUBREDDIT
SUBREDDIT = os.environ.get("SUBREDDIT")

db.connect(host=RETHINKDB_HOST, port=28015, db=RETHINKDB_DB, user=RETHINKDB_USER, password=RETHINKDB_PASSWORD).repl()

reddit = praw.Reddit(client_id=REDDIT_CLIENT_ID,
                     client_secret=REDDIT_CLIENT_SECRET,
                     refresh_token=REDDIT_CLIENT_REFRESH_TOKEN,
                     user_agent='/r/low_poly bot by /u/RenegadeAI')

print('Successfully logged into reddit as {}'.format(reddit.user.me()))

FLAIR_LABELS = ['Blender', 'Unity', 'Modo', '3DS Max', 'Cinema 4D', 'Maya', '<other>']

FOOTER = "\n\n\n[^(REPORT A PROBLEM)](https://www.reddit.com/message/compose?to=RenegadeAI&subject=yo+dawg+low_poly+is+broke!1!!1&message=%3Cplz+replace+this+with+a+description+of+the+error%2C+links%2C+screenshots%2C+mothers+maiden+name%2C+SSN%2C+credit+card+number+and+pin%2C+name+of+first+pet%2C+etc+thx+bye%3E) ^\\\\\\ [^(GITHUB)](https://github.com/notderw/lowpoly)"

subreddit = reddit.subreddit(SUBREDDIT)

meta = objdict(list(db.table('meta').run())[0])

now = datetime.utcnow()

monthrange = calendar.monthrange(now.year,now.month)[1]
month = now.month
day = now.day

month_name = calendar.month_name[month]
time = now.time().replace(microsecond=0)

next_month_name = calendar.month_name[now.month % 12 + 1]

last_month = month - 1 or 12
last_month_name = calendar.month_name[last_month]
last_month_year = now.year - 1 if last_month == 12 else now.year

def update_meta(data):
    try:
        db.table('meta').update(data).run()

    except Exception as e:
        print('Error updating meta {}'.format(e))

def upload_submissions():
    # reddit won't fuck off with changing search, so now we have to
    # nake it look ugly and have an identifier in the flair text
    # since they deprecated searching by css class

    submissions = [{
        'image': get_image(submission),
        'url': submission.shortlink,
        'title': re.sub("\[[^]]*\]", '', submission.title),
        'author': submission.author.name
    } for submission in subreddit.search('flair:"*{} {} SUBMISSION"'.format(last_month_name, last_month_year))]

    db.table('submissions').delete().run()
    db.table('submissions').insert(submissions).run()

def get_image(submission):
    url = submission.url.replace('http:', 'https:')

    match = re.match('(https?)\:\/\/(www\.)?(?:m\.)?imgur\.com/a/([a-zA-Z0-9]+)(#[0-9]+)?', url)
    if match:
        album_id = match.group(3)

        response = requests.get('https://api.imgur.com/3/album/{}'.format(album_id), headers={'Authorization': 'Client-ID {}'.format(IMGUR_CLIENT_ID)})

        if response.status_code not in [404, 429, 500]:
            return response.json()['data']['images'][0]['link']

    match = re.match('^https?://(?:www\.)?imgur\.com/gallery/([a-zA-Z0-9]+)', url)
    if match:
        gallery_id = match.group(1)

        response = requests.get('https://api.imgur.com/3/gallery/{}'.format(gallery_id), headers={'Authorization': 'Client-ID {}'.format(IMGUR_CLIENT_ID)})

        if response.status_code not in [404, 429, 500]:
            return response.json()['data']['link']

    match = re.match(r"(?:https?\:\/\/)?(?:www\.)?(?:m\.)?(?:i\.)?imgur\.com\/([a-zA-Z0-9]+)", url)
    if match:
        image_id = match.group(1)

        response = requests.get('https://api.imgur.com/3/image/{}'.format(image_id), headers={'Authorization': 'Client-ID {}'.format(IMGUR_CLIENT_ID)})

        if response.status_code not in [404, 429, 500]:
            return response.json()['data']['link']

    if(url.endswith(tuple(['.png','jpg']))):
        return url

def update_flairs():
    subreddit.flair.link_templates.clear()

    # This is ugly but it works so fuck it
    for label in FLAIR_LABELS + ['{} \\\\ {} {} SUBMISSION'.format(label, month_name , now.year) for label in FLAIR_LABELS]:
        css = label.lower().split(' ')[0]
        editable = False

        if label.startswith('<other>'):
            css = ''
            editable = True

        subreddit.flair.link_templates.add(label, css_class=css, text_editable=editable)

def get_monthly_theme():
        submission = reddit.submission(meta.theme_voting)
        submission.comment_sort = 'top'

        return sorted(submission.comments, key=lambda k: k.score, reverse=True)[0]

def get_winner():
    votes = list(db.table('votes').run())
    submissions = []
    for submission in db.table('submissions').order_by('title').run():
            submission['score'] = sum([1 for vote in votes if vote['vote'] == submission['id']])
            submissions.append(submission)

    return objdict(sorted(submissions, key=lambda k: k['score'], reverse=True)[0])

def substitute_content(original_content, new, marker):
    content = re.sub(r'(\[\]\(#' + marker + '\)).*(\[\]\(/' + marker + '\))', '\\1\\2', original_content, flags=re.DOTALL)
    opening_marker = "[](#" + marker + ")"

    try:
        marker_pos = content.index(opening_marker) + len(opening_marker)
        return content[:marker_pos] + new + content[marker_pos:]

    except ValueError:
        # Substring not found
        print("Marker {} not found in content".format(opening_marker))
        return original_content

def clamp(val, minimum=0, maximum=255):
    if val < minimum:
        return minimum
    if val > maximum:
        return maximum
    return val

def colorscale(hexstr, scalefactor):
    """
    Scales a hex string by ``scalefactor``. Returns scaled hex string.

    To darken the color, use a float value between 0 and 1.
    To brighten the color, use a float value greater than 1.

    >>> colorscale("#DF3C3C", .5)
    #6F1E1E
    >>> colorscale("#52D24F", 1.6)
    #83FF7E
    >>> colorscale("#4F75D2", 1)
    #4F75D2
    """

    hexstr = hexstr.strip('#')

    if scalefactor < 0 or len(hexstr) != 6:
        return hexstr

    r, g, b = int(hexstr[:2], 16), int(hexstr[2:4], 16), int(hexstr[4:], 16)

    r = clamp(r * scalefactor)
    g = clamp(g * scalefactor)
    b = clamp(b * scalefactor)

    return "#%02x%02x%02x" % (int(r), int(g), int(b))

def update_theme(winner):
    new_sidebar = substitute_content(subreddit.mod.settings()['description'],
                                    (
                                       "\n"
                                       "> *{0} monthly winner:*  \n"
                                       "[{1.title:.30}]({2}) by /u/{1.author}\n"
                                       "\n"
                                    ).format(last_month_name, winner, requests.utils.unquote(winner.url)),
                                    'BOTWINNER'
                                )

    subreddit.mod.update(description=new_sidebar)

    #################################################################
    # Download the winner image, resize it, and upload it to reddit
    #################################################################

    response = requests.get(requests.utils.unquote(winner.image), stream=True, headers={'User-agent': 'Mozilla/5.0'})
    response.raw.decode_content = True

    header_image = Image.open(response.raw).convert('RGB')

    ratio = 1920 / header_image.size[0]

    header_image = header_image.resize((int(header_image.size[0] * ratio), header_image.size[1] * 1), Image.ANTIALIAS)
    top = int(header_image.size[1] / 2) - 260
    header_image = header_image.crop((0, top, 1920, top + 416))

    # If we just use im.tostring() we get a massive image that won't upload
    def toJPEG(im):
        with BytesIO() as f:
            im.save(f, format='JPEG')
            return f.getvalue()

    # PRAW doesn't support raw image uploads so we will do it ourselves
    # https://github.com/praw-dev/praw/blob/a75ebcf934fb49a6966a04d172fa00e957836958/praw/models/reddit/subreddit.py#L1874
    url = praw.const.API_PATH['upload_image'].format(subreddit=subreddit.display_name)
    subreddit._reddit.post(url,
                           data={
                               'name': 'headerimg',
                               'upload_type': 'img',
                               'img_type': 'jpg'
                           },
                           files={'file': toJPEG(header_image)})

    #################################################################
    #Calulate colors, and upload the custom stylesheet bit
    #################################################################

    cc = ColorCube(avoid_color=[255, 255, 255], distinct_threshold=0.8)
    image = header_image.resize((50, 50))
    primary = '#%02x%02x%02x' % tuple(cc.get_colors(image)[0])

    bot_style = (
        ".side .titlebox .md h3 a, .drop-choices a.choice:hover, .submit-page #newlink.submit.content ul.tabmenu.formtab,"
        ".submit_text.enabled.roundfield, body .btn, body button, .content .infobar,"
        "form input[type=checkbox]:checked + label:before, .pretty-form input[type=checkbox]:checked + label:before,"
        ".titlebox .fancy-toggle-button .active.add,"
        ".reddit-infobar.with-icon.locked-infobar, .reddit-infobar.with-icon.locked-infobar:before,"
        ".flair, .side .md>blockquote:first-of-type a:hover:after {{background-color: {color.primary}}}"

        ".side .titlebox .md h3 a:hover, .btn:hover, body button:hover, .titlebox .fancy-toggle-button .active.add:hover {{background-color: {color.hover};}}"

        ".side .titlebox .md h3 a:active , .btn:active , body button:active, .titlebox .fancy-toggle-button .active.add:active {{background-color: {color.active};}}"

        ".thing .title.loggedin.click, .thing .title.click, .thing .title.loggedin, .thing .title, .link .entry .buttons li a.comments,"
        ".link .entry .buttons li a.flairselectbtn, .link .entry .buttons li a:hover, .titlebox .tagline a.flairselectbtn, .md a,"
        ".side .titlebox .md h4 a, .wiki-page .wiki-page-content .md.wiki h4, .sidebox.create .morelink a, a, .side:after, .usertext .bottom-area a.reddiquette,"
        ".wiki-page .pageactions .wikiaction-current, .tagline .submitter, .combined-search-page .search-result .search-result-header .search-title, "
        ".combined-search-page .search-result a, .combined-search-page .search-result a>mark, .combined-search-page .search-result .search-comments, "
        ".flairselector h2, .linefield .title, body .content .sitetable .link .title a:hover, .link .entry .tagline a:hover, .comment .author:hover,"
        ".morelink a, .morelink:hover a, #header .tabmenu li.selected a, form input[type=checkbox]:checked + label, .pretty-form input[type=checkbox]:checked + label, .side .md>blockquote:first-of-type a {{color: {color.primary};}}"

        "form input[type=checkbox]:checked + label:before, .pretty-form input[type=checkbox]:checked + label:before {{ border-color: {color.primary};}}"

        "body .content .roundfield textarea:focus,  body .content input[type=text]:focus, body .content input[type=url]:focus, .roundfield input[type=password]:focus, .roundfield input[type=number]:focus {{ border-color: {color.primary}; box-shadow: 0 1px 0 0 {color.primary};}}"
        ).format(color = objdict({
                                'primary': primary,
                                'hover': colorscale(primary, 1.1),
                                'active': colorscale(primary, .75),
                            }))

    stylesheet = substitute_content(subreddit.stylesheet().stylesheet,
                                    bot_style,
                                    'POLYGONAUTOMATON'
                                )

    subreddit.stylesheet.update(stylesheet)

def main():
    if(day == monthrange - 7):
        submission = subreddit.submit("{} theme voting".format(next_month_name),
                                      selftext = ("Hello everyone, please comment your suggestion for next months theme.\n\n"
                                                  "The deadline for voting will be the end of {0} {1}.\n\n"
                                                  "Please limit your response to only your theme idea, I am not a smart bot."
                                                  + FOOTER
                                                 ).format(month_name, monthrange)
                          )

        submission.mod.contest_mode()
        submission.mod.sticky()

        update_meta({'theme_voting': submission.id})

    elif(day == 1):
        try:
            reddit.submission(meta.monthly_winner).mod.sticky(state=False)
            reddit.submission(meta.theme_voting).mod.sticky(state=False)
            reddit.submission(meta.theme_voting).mod.lock()

        except Exception as e:
            print('ERROR: {}'.format(e))

        upload_submissions()

        submission = subreddit.submit("{} voting now open! Click here to pick your favourite submission!".format(last_month_name),
                                      url = "https://lowpoly.synesis.co/"
                                      )
        submission.mod.sticky()

        update_meta({'voting': submission.id})

        winner_comment = get_monthly_theme()

        submission = subreddit.submit("{0} monthly theme: {1}".format(month_name, winner_comment.body),
                                      selftext = (
                                          "This months theme is {0.body} as suggested by /u/{0.author}\n"
                                          "\n"
                                          "Submissions will be due at the end of the month"
                                          + FOOTER
                                          ).format(winner_comment)
                                      )

        submission.mod.sticky()

        update_meta({'theme': submission.id})

        update_flairs()

    elif(day == 8):
        winner = get_winner()

        submission = subreddit.submit("{0} monthly winner: {1}".format(last_month_name, winner.author),
                                      selftext = (
                                              "Thanks to everyone who participated in last month's challenge.\n"
                                              "\n"
                                              "{0}'s winner is /u/{1.author}, with their submission: [{1.title}]({2})"
                                              + FOOTER
                                          ).format(last_month_name, winner, requests.utils.unquote(winner.url))
                                     )

        try:
            submission.mod.sticky(bottom=False)

        except Exception as e:
            print('ERROR: {}'.format(e))

        update_meta({'monthly_winner': submission.id})

        update_theme(winner)

if __name__ == '__main__':
    main()