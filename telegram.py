import os
import requests
import uuid
import redis

from flask import Flask, jsonify, request
from fuzzywuzzy import fuzz
from ConfigParser import RawConfigParser

here = os.path.dirname(__file__)
parser = RawConfigParser()
parser.read(os.path.join(here, "config"))

API_KEY = parser.get("config", "api_key")
WEBHOOK_SERVER = parser.get("config", "webhook_server")
JEOPARDY_SERVER = parser.get("config", "jeopardy_server")
REDIS_NAMESPACE = parser.get("config", "redis_namespace")
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
    
def format_question(json):
    return "{0} ${1}:\nCategory: {2}\n{3}\n{4}".format(json["round"], json["value"], json["category"], json["date"], json["question"])
    
def filter_words(text):
    filtered_words = [x for x in text.split() if x not in BANNED_WORDS]
    if len(filtered_words) > 0:
        return " ".join(filtered_words)
    else:
        return text
    
def response_correct(response, answer):
    return fuzz.token_sort_ratio(filter_words(response), filter_words(answer)) > 70
    
id = uuid.uuid4()
register_webhook(id)
current_question = {}
redis_conn = redis.StrictRedis()
print id

@register_command("jeopardy")
def jeopardy(parameters, message_id, chat_id, name):
    current_question[chat_id] = get_question()
    return format_message(chat_id, format_question(current_question[chat_id]))
    
@register_command("whatis", "whois")
def answer_question(parameters, message_id, chat_id, name):
    if current_question[chat_id] is None:
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
def giveup(parameters, message_id, chat_id, name):
    if current_question[chat_id] is None:
        return ""
    result = format_message(chat_id, "Correct repsonse was: {0}".format(current_question[chat_id]["answer"]), None)
    current_question[chat_id] = None
    return result
    
@register_command("score")
def get_score(parameters, message_id, chat_id, name):
    keys = redis_conn.keys("{0}:{1}:*".format(REDIS_NAMESPACE, chat_id))
    names = [x.split(":")[-1] for x in keys]
    score_vals = [redis_conn.get(x) for x in keys]
    scores = ["{0}: {1}".format(name, score) for name, score in zip(names, score_vals)]
    score_string = "Scores:\n{0}".format("\n".join(scores))
    return format_message(chat_id, score_string)

@app.route("/{0}".format(id), methods=['POST'])
def get_updates():
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
    split = text.lower().strip().split(' ', 1)
    command = split[0][1:].split("@")[0] if split[0].startswith("/") else None
    parameters = split[1] if len(split) > 1 else None
    if command in command_dict:
        return command_dict[command](parameters, message_id, chat_id, name)
    return ""