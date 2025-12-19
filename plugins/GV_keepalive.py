import datetime
import logging
import dataclasses
import json
import random
import re
import time
from telegram import Bot, ParseMode, Update
from telegram.ext import (Updater, CommandHandler, MessageHandler, ConversationHandler, Filters, CallbackContext)
from pysondb import db as pysondb

from plugins.base_plugin import PluginBase
from utils import emailconf
from utils.emailconf import getEmailConf, EmailConf, emailDB
from utils.mail import Email
from utils.smtpclient import send_email

logger = logging.getLogger(__name__)

gvMailsDB = pysondb.getDb("conf/gv_mails.json")
@dataclasses.dataclass
class GVMailInfo():
    email_addr: str
    gv_number: str # phone number without national code and spaces and dashes (like 3334445555)
    gv_tags: str
    last_send_time: int = 0
    email_peers: dict[str, str] = dataclasses.field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, dic):
        if 'id' in dic:
            dic.pop('id')
        return cls(**dic)

def getGVMailInfo(email_addr):
    ret = gvMailsDB.getByQuery({'email_addr': email_addr})
    if not ret:
        return None
    assert len(ret) == 1
    gvMailDict = ret[0]
    return GVMailInfo.from_dict(gvMailDict)

GV_BODY_PREFIX = "bro hey it's weekend again, the sh*tting work is making me mad, but thanks god it's over now."

def sendReplyGV(emailConf, peer_number, peer_mail):
    logger.info("send SMS to %s (%s)", peer_number, peer_mail)
    subject = 'Re: New text message from %s' % peer_number
    RANDOM_FIRST_NAMES = ['Jane', 'Mark', 'Jennifer', 'Judy', 'Tom', 'Jerry', 'Alice', 'Bob', 'Eve', 'Mallory']
    body = f"{GV_BODY_PREFIX} this time we have a new friend {random.choice(RANDOM_FIRST_NAMES)}, come and enjoy :)"
    send_email(
        smtp_server_uri=emailConf.smtp_server_uri,
        sender_email=emailConf.email_addr, 
        password=emailConf.email_passwd,
        receiver_email=peer_mail,
        subject=subject, body=body)

def gv_periodic_task():
    logger.info('Running gv_periodic_task!')
    try:
        gvMails = [GVMailInfo.from_dict(gvMailDict) for gvMailDict in gvMailsDB.getAll()]
        gvMailsDict = {gvMail.email_addr: gvMail for gvMail in gvMails}
        gvNumbersDict = {gvMail.gv_number: gvMail for gvMail in gvMails}
        logger.info('Total %d GV mails: %s', len(gvMailsDict), list(gvMailsDict.keys()))
        for gvMail in gvMails:
            email_addr = gvMail.email_addr
            emailConf = getEmailConf(email_addr)
            if not emailConf:
                logger.info('GV email %s does not exists, skipping', email_addr)
                continue
            
            if time.time() - gvMail.last_send_time < 7 * 24 * 60 * 60:
                logger.info('email %s last send time %s, skipping', email_addr, datetime.datetime.fromtimestamp(gvMail.last_send_time))
                continue
            logger.info(f"send SMS for GV {email_addr} (number %s)", gvMail.gv_number)
            for peer_number, peer_mail in gvMail.email_peers.items():
                if peer_number in gvNumbersDict:
                    logger.info(f"    send SMS for GV {email_addr} (number %s) -> %s", gvMail.gv_number, peer_number)
                    sendReplyGV(emailConf, peer_number, peer_mail)
                
            gvMail.last_send_time = time.time()
            gvMailsDB.updateByQuery({'email_addr': email_addr}, dataclasses.asdict(gvMail))
    except:
        logger.exception('Error in gv_periodic_task', exc_info=True)

class PluginGV(PluginBase):
    def __init__(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        self.scheduler = BackgroundScheduler()
    
    def onSetup(self, updater):
        logger.info('[GV] initializing...')
        updater.dispatcher.add_handler(CommandHandler("list_gv_mail", self.gv_list_mail))
        updater.dispatcher.add_handler(CommandHandler("add_gv_mail", self.gv_add_mail))
        updater.dispatcher.add_handler(CommandHandler("del_gv_mail", self.gv_del_mail))
        updater.dispatcher.add_handler(CommandHandler("trigger_gv_send", self.gv_trigger_send))
        
        self.scheduler.add_job(gv_periodic_task, 'interval', hours=1, id='GV-periodic_task', replace_existing=True)
        self.scheduler.start()

    def onNewEmail(self, email_addr, email: Email) -> bool:
        logger.info('plugin onNewEmail called!')
        
        sender_name, sender_email = email.sender
        if not sender_email.endswith('@txt.voice.google.com'):
            return False

        m = re.match(r'^(\d+)\.(\d+)\.(.*?)@txt.voice.google.com$', sender_email)
        if not m:
            logger.info('Invalid @txt.voice.google.com domain email: %s', sender_email)
            return False
        
        receiver_number, sender_number, _ = m.groups()
        logger.info('Got GV SMS email %s -> %s', receiver_number, sender_number)
        assert receiver_number.startswith('1') and sender_number.startswith('1')
        sender_gv_number = sender_number[1:]
        
        needIntercept = False
        if GV_BODY_PREFIX.encode() in email.msg_content:
            needIntercept = True

        gvMail = getGVMailInfo(email_addr)
        if not gvMail:
            logger.info('Got GV email, but current receiver email %s is not managed by GV keepalive plugin, ignoring', email_addr)
            return needIntercept

        assert receiver_number == '1' + gvMail.gv_number

        logger.info('Adding GV Peer for %s (%s -> %s)', email_addr, sender_gv_number, sender_email)
        gvMail.email_peers[sender_gv_number] = sender_email
        gvMailsDB.updateByQuery({"email_addr": email_addr}, {"email_peers": gvMail.email_peers}) 
        
        if not needIntercept and 'broadcaster' in gvMail.gv_tags: # don't send reply when it's already autoreply
            emailConf = getEmailConf(email_addr)
            sendReplyGV(emailConf, sender_gv_number, sender_email)
            needIntercept = True
        return needIntercept

    def gv_list_mail(self, update: Update, context: CallbackContext) -> None:
        msg = 'List of GV records:\n'
        for gvMailDict in gvMailsDB.getAll():
            gvMail = GVMailInfo.from_dict(gvMailDict)
            msg += f'    Email: `{gvMail.email_addr}` Number: `{gvMail.gv_number}` Tags: `{gvMail.gv_tags}`\n'
            msg += f'        Peers: {list(gvMail.email_peers.keys())}\n'
        update.message.reply_markdown_v2(msg)

    def gv_add_mail(self, update: Update, context: CallbackContext) -> None:
        if context.args is None or len(context.args) < 2:
            update.message.reply_text('need to supply email_addr and gv_number')
            return
        email_addr = context.args[0]
        gv_number = context.args[1]
        gv_tags = ""
        if len(context.args) >= 3:
            gv_tags = context.args[2]
        
        if not re.match(r'^\d{10}$', gv_number):
            update.message.reply_text('invalid gv number format, should be in format 3334445555')
            return
        ret = gvMailsDB.getByQuery({'email_addr': email_addr})
        if not ret:
            gvMail = GVMailInfo(email_addr=email_addr, gv_number=gv_number, gv_tags=gv_tags, last_send_time=0, email_peers={})
            gvMailsDB.add(dataclasses.asdict(gvMail))
            update.message.reply_text("Successfully add GV mail")
        else:
            gvMailDict = ret[0]
            if 'id' in gvMailDict:
                gvMailDict.pop('id')
            gvMailDict.update({'gv_number': gv_number, 'gv_tags': gv_tags})
            gvMailsDB.updateByQuery({'email_addr': email_addr}, gvMailDict)
            update.message.reply_text("GV mail already exists, updated attributes")

    def gv_del_mail(self, update: Update, context: CallbackContext) -> None:
        if context.args is None or len(context.args) < 1:
            update.message.reply_text('need to supply email_addr')
            return
        email_addr = context.args[0]
        ret = gvMailsDB.getByQuery({'email_addr': email_addr})
        if ret:
            gvMailsDB.deleteById(ret[0]['id'])
            update.message.reply_text("Successfully deleted GV mail %s" % email_addr)
        else:
            update.message.reply_text("GV mail %s does not exists" % email_addr)

    def gv_trigger_send(self, update: Update, context: CallbackContext) -> None:
        self.scheduler.modify_job('GV-periodic_task', next_run_time=datetime.datetime.now())
        update.message.reply_text("Successfully triggered send!")

PLUGIN = PluginGV()