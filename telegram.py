import os
import requests
import uuid

from flask import Flask, jsonify, request
from fuzzywuzzy import fuzz
from ConfigParser import RawConfigParser

here = os.path.dirname(__file__)
parser = RawConfigParser()
parser.read(os.path.join(here, "config"))

API_KEY = parser.get("config", "api_key")
WEBHOOK_SERVER = parser.get("config", "webhook_server")
JEOPARDY_SERVER = parser.get("config", "jeopardy_server")
BANNED_WORDS = ["a", "the", "of", "and", "&"]

app = Flask(__name__)

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
    
def send_message(id, text, reply_to = None):
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
    
def response_correct(response, answer):
    filtered_words = [x for x in answer.split() if x not in BANNED_WORDS]
    if len(filtered_words) > 0:
        answer_filtered = " ".join(filtered_words)
    else:
        answer_filtered = answer
    return fuzz.partial_ratio(response, answer_filtered) > 70
    
id = uuid.uuid4()
register_webhook(id)
current_question = {}
scoreboard = {}
print id

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
    if text.lower().strip().startswith("/jeopardy"):
        current_question[chat_id] = get_question()
        return send_message(chat_id, format_question(current_question[chat_id]))
    if (text.lower().strip().startswith("/whois ") or text.lower().strip().startswith("/whatis "))  and current_question[chat_id] is not None:
        response = text.lower().strip().split(" ", 1)[-1]
        if chat_id not in scoreboard:
            scoreboard[chat_id] = {}
        if name not in scoreboard[chat_id]:
            scoreboard[chat_id][name] = 0
        if response_correct(response, current_question[chat_id]["answer"].lower().strip()):
            result = send_message(chat_id, "Correct", message_id)
            scoreboard[chat_id][name] += current_question[chat_id]["value"]
            current_question[chat_id] = None
            return result
        else:
            scoreboard[chat_id][name] -= current_question[chat_id]["value"]
            return send_message(chat_id, "Incorrect", message_id)
    if text.lower().strip().startswith("/giveup") and current_question[chat_id] is not None:
        result = send_message(chat_id, "Correct repsonse was: {0}".format(current_question[chat_id]["answer"]), None)
        current_question[chat_id] = None
        return result
    if text.lower().strip().startswith("/score"):
        if chat_id in scoreboard:
            scores = ["{0}: {1}".format(name, score) for name, score in scoreboard[chat_id].items() ]
            score_string = "Scores:\n{0}".format("\n".join(scores))
            return send_message(chat_id, score_string)
    return ""