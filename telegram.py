import json
import os
import re
import redis
import requests
import traceback
import uuid

from flask import Flask, jsonify, request
from fuzzywuzzy import fuzz
from ConfigParser import RawConfigParser

here = os.path.dirname(__file__)
parser = RawConfigParser()
parser.read(os.path.join(here, "config"))

changelog_path = os.path.join(here, "changelog")

API_KEY = parser.get("config", "api_key")
WEBHOOK_SERVER = parser.get("config", "webhook_server")
JEOPARDY_SERVER = parser.get("config", "jeopardy_server")
REDIS_NAMESPACE = parser.get("config", "redis_namespace")
BOT_NAME = parser.get("config", "bot_name").lower()
GITHUB_API_KEY = parser.get("config", "github_api_key")
GITHUB_REPO = parser.get("config", "github_repo")
GITHUB_USER = parser.get("config", "github_user")
BANNED_WORDS = ["a", "the", "of", "and", "&"]

app = Flask(__name__)

command_dict = {}

def register_command(*commands):
    def register(func):
        for command in commands:
            command_dict[command] = func
        def returned_wrapper(*args, **kwargs):
            return func(*args, **kwargs)
    return register

def make_request(method, parameters):
    endpoint = "https://api.telegram.org/bot{0}/{1}".format(API_KEY, method)
    resp = requests.post(endpoint, parameters)
    resp.raise_for_status()
    json = resp.json()
    if not json["ok"]:
        raise Exception(json["description"])
    return json

def register_webhook(id):
    path = "{0}/{1}".format(WEBHOOK_SERVER, id)
    resp = make_request("setWebhook", {"url": path })
    print resp
    
def format_message(id, text, reply_to = None):
    parameters = {"method": "sendMessage", "chat_id": id, "text": text}
    if reply_to is not None:
        parameters["reply_to_message_id"] = reply_to
    return jsonify(parameters)
    
def get_question():
    resp = requests.get(JEOPARDY_SERVER)
    resp.raise_for_status()
    json = resp.json()
    return json
    
def format_question(json, last_answer):
    return u"{5}{0} ${1}:\nCategory: {2}\n{3}\n{4}".format(json["round"], json["value"], json["category"], json["date"], json["question"], "Last Answer: {0}\n".format(last_answer) if last_answer is not None else "")
    
def filter_words(text):
    filtered_words = [x for x in text.split() if x not in BANNED_WORDS]
    if len(filtered_words) > 0:
        return " ".join(filtered_words)
    else:
        return text
        
def strip_brackets(text):
    return re.sub(r'\([^)]*\)', '', text)
    
def response_correct(response, answer):
    score = max(
        fuzz.token_sort_ratio(filter_words(response), filter_words(answer)),
        fuzz.token_sort_ratio(filter_words(response), strip_brackets(filter_words(answer)))
    )
    return score > 70
    
def post_issue(title, body):
    report = {"title": title.title(), "body": body, "labels": ["auto_created"]}
    return requests.post("https://api.github.com/repos/{0}/{1}/issues".format(GITHUB_USER, GITHUB_REPO), params={"access_token": GITHUB_API_KEY}, data=json.dumps(report))
    
id = uuid.uuid4()
register_webhook(id)
last_question = {}
current_question = {}
redis_conn = redis.StrictRedis()
print id

@register_command("jeopardy")
def jeopardy(chat_id, **kwargs):
    last_answer = current_question[chat_id]["answer"] if current_question[chat_id] is not None else None
    current_question[chat_id] = get_question()
    last_question[chat_id] = json.dumps(current_question[chat_id], indent=4, separators=(',', ': '))
    return format_message(chat_id, format_question(current_question[chat_id], last_answer))
    
@register_command("whatis", "whois")
def answer_question(chat_id, name, message_id, parameters, **kwargs):
    if current_question[chat_id] is None:
        return ""
    if parameters is None:
        return ""
    if response_correct(parameters, current_question[chat_id]["answer"].lower().strip()):
        result = format_message(chat_id, "Correct", message_id)
        redis_conn.incr("{0}:{1}:{2}".format(REDIS_NAMESPACE, chat_id, name), current_question[chat_id]["value"])
        current_question[chat_id] = None
        return result
    else:
        redis_conn.decr("{0}:{1}:{2}".format(REDIS_NAMESPACE, chat_id, name), current_question[chat_id]["value"])
        return format_message(chat_id, "Incorrect", message_id)
    
@register_command("giveup")
def giveup(chat_id, **kwargs):
    if current_question[chat_id] is None:
        return ""
    result = format_message(chat_id, "Correct repsonse was: {0}".format(current_question[chat_id]["answer"]), None)
    current_question[chat_id] = None
    return result
    
@register_command("score")
def get_score(chat_id, **kwargs):
    keys = redis_conn.keys("{0}:{1}:*".format(REDIS_NAMESPACE, chat_id))
    names = [x.split(":")[-1] for x in keys]
    score_vals = [redis_conn.get(x) for x in keys]
    scores = ["{0}: {1}".format(name, score) for name, score in zip(names, score_vals)]
    score_string = "Scores:\n{0}".format("\n".join(scores))
    return format_message(chat_id, score_string)
    
@register_command("version")
def get_version(chat_id, **kwargs):
    with open(changelog_path, "rb") as f:
        version = f.readline()
        return format_message(chat_id, version)
        
@register_command("changelog")
def get_changelog(chat_id, **kwargs):
    with open(changelog_path, "rb") as f:
        lines = []
        for line in f:
            if line.startswith("------"):
                break
            lines.append(line.strip())
        return format_message(chat_id, "\n".join(lines))
        
@register_command("flag")
def flag_error(chat_id, message_id, parameters, name, **kwargs):
    if last_question[chat_id] is None:
        return format_message(chat_id, "Unable to file an error report, no question found")
    if parameters is None:
        return format_message(chat_id, "Please provide a reason for this error report")
    resp = post_issue(parameters, "Reported by: {0}\nRaw Data:\n{1}".format(name, last_question[chat_id]))
    if resp.status_code == 201:
        url = resp.json()["html_url"]
        current_question[chat_id] = None
        return format_message(chat_id, "Error report filed successfully. You can track the issue here: {0}".format(url))
    else:
        try: 
            data = resp.json()
            err = "\n".join([x for x in data["message"].split("\n") if x])
        except (ValueError, KeyError):
            err = resp.reason
        return format_message(chat_id, "Unable to file report.  Reason:\n\n{0}".format(err))

@app.route("/{0}".format(id), methods=['POST'])
def get_updates():
    try:
        json = request.get_json()
        try:
            message = json["message"]
            text = message["text"]
            message_id = message["message_id"]
            chat_id = message["chat"]["id"]
            user= message["from"]
            name = user["first_name"]
            if "last_name" in user:
                name += " " + user["last_name"]
        except KeyError:
            return ""
        if not chat_id in current_question:
            current_question[chat_id] = None
        if not chat_id in last_question:
            last_question[chat_id] = None
        split = text.lower().strip().split(' ', 1)
        command = split[0][1:] if split[0].startswith("/") else None
        if command is not None and "@" in command:
            command, bot = command.split("@", 1)
            if bot != BOT_NAME:
                return ""
        parameters = split[1] if len(split) > 1 else None
        if command in command_dict:
            return command_dict[command](chat_id=chat_id, name=name, parameters=parameters, message_id=message_id)
        return ""
    except Exception:
        resp = post_issue("Crash Report", "```\n{0}\n```".format(traceback.format_exc()))
        if resp.status_code == 201:
            url = resp.json()["html_url"]
            current_question[chat_id] = None
            return format_message(chat_id, "Sorry, an unexpected error occurred. An error report has been automatically generated and is available here: {0}".format(url))
        else:
            traceback.print_exc()
            try: 
                data = resp.json()
                err = "\n".join([x for x in data["message"].split("\n") if x])
            except (ValueError, KeyError):
                err = resp.reason
            print err
            return format_message(chat_id, "Sorry, an unexpected error occurred.  Generation of an error report failed, but the error has been logged")