from tornado.wsgi import WSGIContainer
from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop
from telegram import app
import os

from ConfigParser import RawConfigParser

here = os.path.dirname(__file__)
parser = RawConfigParser()
parser.read(os.path.join(here, "config"))

http_server = HTTPServer(WSGIContainer(app), ssl_options={
        "certfile": parser.get("config", "cert_file"),
        "keyfile": parser.get("config", "key_file"),
    })
http_server.listen(parser.get("config", "port"))
IOLoop.instance().start()