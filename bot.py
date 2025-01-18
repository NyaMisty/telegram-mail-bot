import datetime
import logging
import os
import re
import socket
import time
from traceback import format_exc
from typing import Type
import dataclasses
import typing
from telegram import Bot, ParseMode, Update
from telegram.constants import MAX_MESSAGE_LENGTH
from telegram.ext import (Updater, CommandHandler, MessageHandler, ConversationHandler, Filters, CallbackContext)
from pysondb import db as pysondb
from utils import EmailClientBase, EmailClientIMAP, EmailClientPOP3
from utils.oauth2_helper import OAuth2_MS, OAuth2Factory
from utils.smtpclient import send_email
from utils.conf import Conf

updater: Updater = None # type: ignore[assignment]

socket.setdefaulttimeout(10) # avoid imaplib timeout

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s',
                    # stream=sys.stdout,
                    level=logging.INFO)
logger = logging.getLogger(__name__)

emailDB = pysondb.getDb("conf/email_accounts.json")
def doEmailDBMigration():
    for confDict in emailDB.getAll():
        curID = confDict.pop('id')

        # migrate001: int chat_id into string
        if 'chat_id' in confDict:
            if isinstance(confDict['chat_id'], int):
                confDict['chat_id'] = str(confDict['chat_id'])
        emailDB.updateById(curID, confDict)

doEmailDBMigration()

def is_owner(update: Update) -> bool:
    if update.message:
        if update.message.chat_id == Conf.OWNER_CHAT_ID:
            return True
        if update.message.from_user is not None and update.message.from_user.id == Conf.OWNER_CHAT_ID:
            return True
    return False

def handle_large_text(text):
    while text:
        if len(text) < MAX_MESSAGE_LENGTH:
            yield text
            text = None
        else:
            out = text[:MAX_MESSAGE_LENGTH]
            yield out
            text = text.lstrip(out)

def error(update: Update, context: CallbackContext) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)

def _help(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    """Send a message when the command /help is issued."""
    help_str = f"""邮箱设置:
/add_email john.doe@example.com password protocol://server[:port] smtp_protocol://smtp_server[:port]
例：
    /add_email john.doe@hotmail.com P@ssw0rd pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
    /add_email john.doe@hotmail.com token:ms:XX_refresh_token_XXX imaps://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
    /add_email john.doe@hotmail.com code:ms:XX_authorization_code_XXX imaps://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
    /add_email john.doe@gmail.com password imaps://imap.gmail.com:993 smtps://smtp.gmail.com
    
/do_oauth john.doe@example.com [provider]
返回OAuth登录链接，例：
    /do_oauth john.doe@hotmail.com
    /do_oauth john.doe@microsoft-organization.com ms-org

/list_email
/del_email john.doe@example.com
/help get help

Telegram中回复即可直接回复邮件
"""
    update.message.reply_text(text=help_str)

@dataclasses.dataclass
class EmailConf():
    email_addr: str
    email_passwd: str
    server_uri: str
    smtp_server_uri: str | None
    chat_id: str
    inbox_num: int

def setting_list_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    
    hidePassword = True
    if context.args:
        hidePassword = False
        
    msg = 'Email Account List:\n'
    for emailConfDict in emailDB.getAll():
        try:
            emailConf = getEmailConfFromDict(emailConfDict)
        except Exception:
            msg += "    (Invalid Email Account: %s)\n" % emailConfDict
            continue
        pwd = emailConf.email_passwd
        if hidePassword:
            pwd =  len(emailConf.email_passwd) * '*'
        msg += f"    Email: {emailConf.email_addr}, Password: {pwd}, Server: {emailConf.server_uri}, SMTP Server: {emailConf.smtp_server_uri}, InboxNum: {emailConf.inbox_num}\n"
    update.message.reply_text(msg)

def setting_do_oauth(update: Update, context: CallbackContext):
    if not is_owner(update):
        return
    if not context.args:
        update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    oauth_type = None
    if len(context.args) >= 2:
        oauth_type = context.args[1]
    
    if not oauth_type:
        oauth_type = OAuth2Factory.detect_provider(email_addr)
        if not oauth_type:
            update.message.reply_text("Cannot autodetect oauth provider for email %s, please specify the provider." % email_addr)
            return

    oauthProvider = OAuth2Factory.get_provider(oauth_type)
    url = oauthProvider.get_login_url(email_addr)
    update.message.reply_markdown_v2(f"OAuth2 Provider: {oauth_type}\n\nUsing this url to login: `{url}`\nOr [click here]({url})")
    return

def setting_add_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    if not context.args:
        update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    email_passwd = context.args[1]
    email_server = context.args[2]
    email_smtp = None
    if len(context.args) > 3:
        email_smtp = context.args[3]
    
    if not email_server.startswith('imap') and not email_server.startswith('pop3'):
        update.message.reply_text(f"invalid server: {email_server}")
        return
    if email_smtp and not email_smtp.startswith('smtp'):
        update.message.reply_text(f"invalid smtp server: {email_smtp}")
        return
    
    message_thread_id = ""
    if update.message.is_topic_message and update.message.message_thread_id:
        message_thread_id = update.message.message_thread_id
    
    emailConf = EmailConf(
        email_addr=email_addr,
        email_passwd=email_passwd,
        server_uri=email_server,
        smtp_server_uri=email_smtp,
        chat_id=f"{update.message.chat_id},{message_thread_id}",
        inbox_num=-1,
    )
    
    logger.info("received setting_email command.")
    
    new_passwd = OAuth2Factory.code_to_token(s=email_passwd)
    if new_passwd:
        update.message.reply_text(f"Exchanged refresh_token {new_passwd} from {email_passwd} for email {email_addr}, Rewriting password~")
        emailConf.email_passwd = email_passwd = new_passwd

    with getEmailClient(emailConf) as client:
        inbox_num = client.get_mails_count()
    emailConf.inbox_num = inbox_num
    
    if emailDB.getByQuery({'email_addr': email_addr}):
        update.message.reply_text(f"Email {email_addr} is already configured! Overriding...")
        emailDB.updateByQuery({'email_addr': email_addr},  dataclasses.asdict(emailConf))
    else:
        emailDB.add(dataclasses.asdict(emailConf))
    
    update.message.reply_text("Configure email success!")


def setting_del_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    if not context.args:
        update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    
    emails = emailDB.getByQuery({'email_addr': email_addr})
    if not emails:
        update.message.reply_text(f'cannot find email account: {email_addr}')
        return
    assert len(emails) == 1
    pk = emails[0][emailDB.id_fieldname]
    assert emailDB.deleteById(pk)
    update.message.reply_text(f'Successfully deleted email account {email_addr}')

def safeSendText(sender, content):
    for text in handle_large_text(content):
        safeSend(sender, text)

def safeSend(sender, content):
    for i in range(10):
        try:
            sender(content)
            break
        except Exception:
            logger.warning('cannot send tg msg (retry %d)', i, exc_info=True)
        time.sleep(i * 5)

def getEmailConfFromDict(emailConfDict):
    if 'id' in emailConfDict:
        emailConfDict.pop('id')
    emailConf = EmailConf(**emailConfDict)
    return emailConf

def getEmailConf(email_addr):
    emailConfs = emailDB.getByQuery({'email_addr': email_addr})
    if not emailConfs:
        raise Exception(f'cannot find config for email {email_addr}')
    assert len(emailConfs) == 1
    
    emailConfDict = emailConfs[0]
    emailConf = getEmailConfFromDict(emailConfDict)
    return emailConf

emailClientCache: dict[tuple, EmailClientBase] = {}
def getEmailClient(emailConf: EmailConf) -> EmailClientBase:
    cacheKey = tuple(dataclasses.astuple(emailConf))
    emailClient: EmailClientBase | None = emailClientCache.get(cacheKey, None)
    if emailClient:
        try:
            emailClient.refresh_connection()
            logger.info(f'email client for {emailConf.email_addr} is still good')
            return emailClient
        except Exception as e:
            logger.info(f'email client for {emailConf.email_addr} is invalid ({str(e)}), re-creating...')
            emailClient = None

    EmailClient: Type[EmailClientBase]
    if emailConf.server_uri.startswith('pop3'):
        EmailClient = EmailClientPOP3
    elif emailConf.server_uri.startswith('imap'):
        EmailClient = EmailClientIMAP
    else:
        raise Exception(f"invalid email server_uri: {emailConf.server_uri}")
    
    emailClient = EmailClient(emailConf.email_addr, emailConf.email_passwd, emailConf.server_uri)
    emailClientCache[cacheKey] = emailClient
    return emailClient

PERIODIC_TASK_ERRORS: dict[str, dict[str, list[str]]] = {
    
}
PERIODIC_TASK_TICK = 0
def periodic_task() -> None:
    # {
    #     'email_addr': email_addr,
    #     'email_passwd': email_passwd,
    #     'server_uri': email_server,
    #     'chat_id': update.message.chat_id,
    # }
    global PERIODIC_TASK_TICK
    logger.info("entered periodic task, tick: %d...", PERIODIC_TASK_TICK)
    PERIODIC_TASK_TICK += 1
    
    # updater.bot.send_message()
    def handler(emailConfDict, managers=None):
        logger.info("processing periodic task for %s", emailConfDict)
        try:
            emailConf = getEmailConfFromDict(emailConfDict)
            email_addr = emailConf.email_addr
        except Exception:
            logger.warning('Cannot parse emailConfDict: %s', emailConfDict, exc_info=True)
            return
        from multiprocessing import get_context, Process, Manager, Queue
        ctx = get_context('fork')
        # def run_with_timeout(fun, *args, **kwargs):
        #     d = Manager().dict()
        #     def wrapper():
        #         d['ret'] = fun(*args, **kwargs)
        #     p: Process = ctx.Process(target=wrapper, args=args, kwargs=kwargs)
        #     p.start()
        #     p.join(request_timeout)
        #     if p.exitcode != 0:
        #         raise RuntimeError("Error during executing run_with_timeout, code: %s" % p.exitcode)
        #     p.kill()
        #     return d['ret']
        #     # cannot use Pool because it will pickle lots of things causing error
        #     # try:
        #     #     pool = ctx.Pool(1)
        #     #     fut = pool.apply_async(fun, args=args, kwds=kwargs)
        #     #     return fut.get(request_timeout)
        #     # finally:
        #     #     pool.close()
        def run_with_timeout(fun, *args, **kwargs):
            pool = ThreadPool(1)
            fut = pool.apply_async(fun, args=args, kwds=kwargs)
            return fut.get(Conf.REQUEST_TIMEOUT)
        try:
            client = getEmailClient(emailConf)
            chat_id, reply_to_message_id = emailConf.chat_id.split(',') if ',' in emailConf.chat_id else (emailConf.chat_id, None)
            new_inbox_num = run_with_timeout(lambda: client.get_mails_count())
            if new_inbox_num < emailConf.inbox_num:
                emailDB.updateByQuery({'email_addr': email_addr}, {'inbox_num': new_inbox_num})
            elif new_inbox_num > emailConf.inbox_num:
                for idx in range(emailConf.inbox_num + 1, new_inbox_num + 1):
                    try:
                        mail = run_with_timeout(lambda: client.get_mail_by_index(idx))
                    except Exception:
                        logger.warning('cannot retrieve mail %d for %s', idx, emailConf, exc_info=True)
                        break
                    
                    text = f'''New Email [{emailConf.email_addr}-{idx}]\n'''
                    emailbody, emailfiles = mail.format_email()
                    text += emailbody
                    
                    run_with_timeout(lambda: safeSendText(
                        lambda text: updater.bot.send_message(chat_id=chat_id,reply_to_message_id=reply_to_message_id, text=text), # type: ignore[has-type]
                        text,
                    ))
                    for filename, filemime, file_content in emailfiles:
                        if filemime.startswith('image'):
                            run_with_timeout(lambda: safeSend(
                                lambda text: updater.bot.send_photo(chat_id=chat_id, reply_to_message_id=reply_to_message_id, photo=file_content, filename=filename), # type: ignore[has-type]
                                text
                            ))
                        else:
                            run_with_timeout(lambda: safeSend(
                                lambda text: updater.bot.send_document(chat_id=chat_id, reply_to_message_id=reply_to_message_id, document=file_content, filename=filename), # type: ignore[has-type]
                                text
                            ))
                    emailDB.updateByQuery({'email_addr': email_addr}, {'inbox_num': idx})
        except Exception as e:
            if re.findall(r'\bEOF\b', str(e)):
                pass # do not process occasional random network issue
            elif re.findall(r'Server Unavailable. 21', str(e)):
                pass # ignore stupid outlook server error
            else:
                if email_addr not in PERIODIC_TASK_ERRORS:
                    PERIODIC_TASK_ERRORS[email_addr] = {}
                exceptionStr = str(e)
                if exceptionStr not in PERIODIC_TASK_ERRORS[email_addr]:
                    PERIODIC_TASK_ERRORS[email_addr][exceptionStr] = []
                PERIODIC_TASK_ERRORS[email_addr][exceptionStr].append(format_exc())
            logger.warning('periodic task error in %s', email_addr, exc_info=True)
    
    # for emailConfDict in emailDB.getAll():
    #     handler(emailConfDict)
    
    # TODO: Implement timeout control
    from multiprocessing.pool import ThreadPool
    for _ in ThreadPool(5).imap_unordered(handler, emailDB.getAll()):
        pass

LAST_ERROR_REPORT_TIME: float | None = None
LAST_ERROR_REPORT_TICK = 0
def periodic_task_error_report():
    global PERIODIC_TASK_ERRORS, LAST_ERROR_REPORT_TIME, LAST_ERROR_REPORT_TICK
    
    last_errors = PERIODIC_TASK_ERRORS
    logger.info("last errors (%s - %s): %s", LAST_ERROR_REPORT_TIME, time.time(), last_errors)
    
    queries = PERIODIC_TASK_TICK - LAST_ERROR_REPORT_TICK

    if LAST_ERROR_REPORT_TIME is not None:
        time_since_last_report = str(datetime.datetime.now() - datetime.datetime.fromtimestamp(Conf.LAST_ERROR_REPORT_TIME))
    else:
        time_since_last_report = str(datetime.timedelta(seconds=Conf.ERR_REPORT_INTERVAL))    

    LAST_ERROR_REPORT_TICK = PERIODIC_TASK_TICK
    LAST_ERROR_REPORT_TIME = time.time()
    PERIODIC_TASK_ERRORS = {}
    
    if not last_errors:
        logger.info("No errors since last error report, skipping this report!")
        return
    
    text = ''
    for acc, accErrDict in last_errors.items():
        if sum(len(errList) for errList in accErrDict.values()) < queries * 0.5:
            # to avoid spamming, we only print error summary if this account has MASSIVE amount of errors
            text += f'Acc: {acc}\n'
            for errType, errList in accErrDict.items():
                text += f'    Error: {errType}, triggered {len(errList)} times\n'
    if not text:
        logger.info("No account have massive errors, skipping this report!")
    text = f'''Error Summary during last {queries} queries in duration {time_since_last_report}:\n''' + text
    safeSendText(
        lambda text: updater.bot.send_message(chat_id=Conf.OWNER_CHAT_ID, text=text), # type: ignore[has-type]
        text
    )

def is_reply_to_bot(update: Update, context: CallbackContext) -> bool:
    assert update.message
    if update.message.reply_to_message:
        if update.message.reply_to_message.from_user:
            if update.message.reply_to_message.from_user.id == context.bot.id:
                return True
    return False


def handle_reply_send_email(update: Update, context: CallbackContext):
    if not update.message.reply_to_message:
        return
    
    if not is_reply_to_bot(update, context):
        return

    original_message = update.message.reply_to_message.text
    email, mail_id, from_email = re.findall(r'^.*?\[(.*?)-(\d+)\]\n[\S\s]+?From: .*?(\S+)\n', original_message)[0]
    reply_message = update.message.text
    subject, split, body = reply_message.partition('\n\n')
    if split != '\n\n':
        update.message.reply_text("Don't know the subject of email. Send the email in this form: \n\n(Your subject here)\n\n(Your body)")
        return
    emailConf = getEmailConf(email)
    if not emailConf.smtp_server_uri:
        update.message.reply_text(f"Cannot send email from {email}, no smtp server configured.")
        return

    send_email(
        smtp_server_uri=emailConf.smtp_server_uri, 
        sender_email=emailConf.email_addr, 
        password=emailConf.email_passwd, 
        receiver_email=from_email, 
        subject=subject, body=body)
    update.message.reply_text(f"Successfully sent the email from {email} to {from_email} with subject {subject}")

def main():
    # Create the EventHandler and pass it your bot's token.
    global updater
    updater = Updater(token=Conf.TELEGRAM_TOKEN, use_context=True)
    print(Conf.TELEGRAM_TOKEN)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    assert dp

    # simple start function
    dp.add_handler(CommandHandler(["start", "help"], _help))
    #
    #  Add command handler to set email address and account.
    dp.add_handler(CommandHandler("list_email", setting_list_email))
    dp.add_handler(CommandHandler("do_oauth", setting_do_oauth))
    dp.add_handler(CommandHandler("add_email", setting_add_email))
    dp.add_handler(CommandHandler("del_email", setting_del_email))
    dp.add_handler(MessageHandler(Filters.reply, handle_reply_send_email))
    # TODO: implement send mail
    # dp.add_handler(ConversationHandler(
    #     entry_points=[CommandHandler("send_email", handle_start_send_email)],
    #     states={
    #         SELECT_SENDER: [MessageHandler(Filters.regex('^[^@]+@[^@]+\.[^@]+$'), handle_send_email_semder)],
    #         INPUT_RECEIVER: [MessageHandler(Filters.regex('^[^@]+@[^@]+\.[^@]+$'), handle_send_email_receiver)],
    #         BODY: [MessageHandler(Filters.text, handle_send_email_body)],
    #     },
    #     fallbacks=[CommandHandler("cancel", cancel)],
    # ))

    
    def errorHandler(update: Update, context: CallbackContext):
        if not update or not update.message:
            return

        import traceback
        if context.error:
            excStr = '\n'.join(traceback.format_exception(context.error))
            update.message.reply_text(f'Error processing command: {excStr}')
        else:
            update.message.reply_text('Error processing command: (unknown error)')
        
    dp.add_error_handler(errorHandler)
    
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(periodic_task, 'interval', seconds=Conf.POLL_INTERVAL, id='email-periodic_task', replace_existing=True)
    scheduler.add_job(periodic_task_error_report, 'interval', seconds=Conf.ERR_REPORT_INTERVAL, id='email-periodic_error_report', replace_existing=True)
    scheduler.start()

    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()
