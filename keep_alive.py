from flask import Flask
from threading import Thread

app = Flask('app')

@app.route('/')
def home():
    return "HTML CODE returned error: На этом сайте ничего нет"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    server = Thread(target=run)
    server.start()
