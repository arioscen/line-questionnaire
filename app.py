# -*- coding: utf-8 -*-

from flask import Flask, request, abort
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import *
import ConfigParser
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pymongo import MongoClient
import json
import datetime


config = ConfigParser.ConfigParser()
config.read('config.ini')

LINE_SECRET = config.get('line', 'secret')
LINE_TOKEN = config.get('line', 'token')
MONGODB_CONN_STRING = config.get('mongodb', 'conn_string')

scope = ['https://spreadsheets.google.com/feeds']
credentials = ServiceAccountCredentials.from_json_keyfile_name('auth.json', scope)
gc = gspread.authorize(credentials)

# 表單類別，sheet1 是資料儲存，sheet2 是問題與回覆
class Quest:
    def __init__(self, number):
        self.key = config.get('sheet', 'key'+str(number))
        self.sheets = gc.open_by_key(self.key)
        self.sheet1 = self.sheets.sheet1
        self.sheet2 = self.sheets.get_worksheet(1)
        self.question_number = self.sheet2.col_count

    def get_question(self, step):
        return self.sheet2.cell(1, step).value

    def get_response(self, step):
        return self.sheet2.cell(2, step).value

    def save_answers(self, answers):
        self.sheet1.insert_row(answers, index=2)

    def get_question_number(self):
        return self.question_number

# 清理標點符號
def clean_string(string):
    new_string = string.encode('utf-8').replace(",", "").replace("\n", "").replace("\r", "").replace("\"", "").replace("\'", "")
    return new_string.decode('utf-8')

# 送出問題，問題後加註 #time 表示要送出時間表單，可繼續擴充
def send_question(user_id, question):
    question_split = question.split("#time")[0]
    if question_split != question:
        buttons_template_message = TemplateSendMessage(
            alt_text='Buttons template',
            template=ButtonsTemplate(
                text=question_split.encode('utf-8'),
                actions=[
                    DatetimePickerTemplateAction(
                        type='datetimepicker',
                        label='選擇時間',
                        data='datetimepicker',
                        mode='datetime',
                        initial='2017-11-01T00:00',
                        max='2017-12-01T00:00',
                        min='2017-10-01T00:00'
                    )
                ]
            )
        )
        line_bot_api.push_message(user_id, buttons_template_message)
    else:
        line_bot_api.push_message(user_id, TextSendMessage(text=question))

# 處理、儲存訊息
def deal_message(event, message):
    user_id = json.loads(str(event.source))['userId']
    profile = line_bot_api.get_profile(user_id)
    message_text = clean_string(message)

    with MongoClient(MONGODB_CONN_STRING) as client:
        db = client['sheets']
        col = db['user']

        # 用號碼分別對應不同的表單
        if message.encode('utf-8') == '問卷一':
            question = quest1.get_question(1)
            send_question(user_id, question)
            col.update({"_id": user_id}, {"$set": {"quest_number": 1, "step": 1, "answers": []}}, upsert=True)
            return 0

        if message.encode('utf-8') == '問卷二':
            question = quest2.get_question(1)
            send_question(user_id, question)
            col.update({"_id": user_id}, {"$set": {"quest_number": 2, "step": 1, "answers": []}}, upsert=True)
            return 0

        user_data = col.find_one({"_id": user_id})
        if user_data and user_data['quest_number'] != 0:
            quest_number = user_data['quest_number']
            # 用號碼取得表單實例
            quest = eval("quest" + str(quest_number))
            step = user_data['step']
            response = quest.get_response(step)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response))

            # 問卷的過程
            if step < quest.get_question_number():
                question = quest.get_question(step+1)
                send_question(user_id, question)
                col.update({"_id": user_id}, {"$inc": {"step": 1}, "$push": {"answers": message_text}})
            # 問卷結束
            else:
                col.update({"_id": user_id}, {"$set": {"quest_number": 0}, "$push": {"answers": message_text}})
                answers = col.find_one({"_id": user_id}, {"answers": 1, "_id": 0})['answers']
                datetime_ = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                display_name = clean_string(profile.display_name)
                try:
                    status_message = clean_string(profile.status_message)
                except AttributeError:
                    status_message = 'None'
                try:
                    picture_url = clean_string(profile.picture_url)
                except AttributeError:
                    picture_url = 'None'
                quest.save_answers(answers + [datetime_, user_id, display_name, status_message, picture_url])
            return 0

        return 1


line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# 表單實例
quest1 = Quest(1)
quest2 = Quest(2)

app = Flask(__name__)


@app.route("/", methods=['GET', 'POST'])
def callback():
    if request.method == 'GET':
        return 'OK'
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    deal_result = deal_message(event, event.message.text)
    if deal_result == 1:
        buttons_template_message = TemplateSendMessage(
            alt_text='Buttons template',
            template=ButtonsTemplate(
                text='請選擇問卷',
                actions=[
                    MessageTemplateAction(
                        label='問卷一',
                        text='問卷一'
                    ),
                    MessageTemplateAction(
                        label='問卷二',
                        text='問卷二'
                    )
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, buttons_template_message)


@handler.add(PostbackEvent)
def postback_message(event):
    deal_message(event, event.postback.params['datetime'])


if __name__ == '__main__':
    app.run(host='0.0.0.0')
