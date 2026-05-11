import asyncio
import datetime
import importlib
import logging
import os
import re
import socket
import time
import dataclasses
from traceback import format_exc
from typing import Type
from multiprocessing.pool import ThreadPool

import telegramify_markdown
from telegram import Bot, ParseMode, Update
from telegram.constants import MAX_MESSAGE_LENGTH
from telegram.ext import (Updater, CommandHandler, MessageHandler, ConversationHandler, Filters, CallbackContext)
from pysondb import db as pysondb

from plugins.base_plugin import PluginBase
from utils import EmailClientBase, EmailClientIMAP, EmailClientPOP3, EmailClientProton
from utils.imap_autodetect import get_mail_server
from utils.oauth2_helper import OAuth2_MS, OAuth2Factory, TokenStore
from utils.smtpclient import send_email
from utils.conf import Conf
from utils.emailconf import EmailConfBase, emailDB, EmailConf, getEmailConf
from utils.telegram_helper import tg_md2_escape

if Conf.DEBUG:
    level = logging.DEBUG
else:
    level = logging.INFO
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s',
                    # stream=sys.stdout,
                    level=level)

updater: Updater = None # type: ignore[assignment]

socket.setdefaulttimeout(10) # avoid imaplib timeout

logger = logging.getLogger(__name__)

def on_oauth_token_update(token_identifier, old_access_token, new_access_token, old_token_payload, new_token_payload):
    provider_name, _, identity = token_identifier.partition(':')
    email_addr, _, _ = identity.partition(':')
    if not provider_name or not email_addr or not new_token_payload:
        return
    email_confs = emailDB.getByQuery({'email_addr': email_addr})
    for email_conf in email_confs:
        old_passwd = email_conf.get('email_passwd')
        if not old_passwd or not old_passwd.startswith(f'token:{provider_name}:'):
            continue
        parsed_provider, current_token_payload, additional_data = OAuth2Factory.parse_token_parts(old_passwd)
        if parsed_provider != provider_name or current_token_payload != old_token_payload:
            continue
        new_passwd = f'token:{provider_name}:{new_token_payload}'
        if additional_data:
            new_passwd = f'{new_passwd}:::{additional_data}'
        emailDB.updateByQuery({'email_addr': email_addr}, {'email_passwd': new_passwd})
        break

TokenStore.token_update_callback = on_oauth_token_update

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
            text = text[MAX_MESSAGE_LENGTH:]

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
例：
    列出邮箱：/list_email
    列出邮箱添加指令：/list_email 1
/del_email john.doe@example.com
/help get help

Telegram中回复即可直接回复邮件
"""
    update.message.reply_text(text=help_str)

def setting_list_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    
    hidePassword = True
    if context.args:
        hidePassword = False
        
    msg = 'Email Account List:\n'
    for emailConfDict in emailDB.getAll():
        try:
            emailConf = EmailConf.from_dict(emailConfDict)
        except Exception:
            msg += "    (Invalid Email Account: %s)\n" % emailConfDict
            continue
        pwd = emailConf.email_passwd
        state = '❌' if emailConf.disabled else '✅'
        if hidePassword:
            pwd =  len(emailConf.email_passwd) * '*'
        if hidePassword:
            msg += f"    {state} Email: `{emailConf.email_addr}`, Server: `{emailConf.server_uri}`, SMTP Server: `{emailConf.smtp_server_uri}`, Mailboxes: `{list(emailConf.mailbox_offsets.keys())}`\n"
        else:
            addCommand = f'/add_email {emailConf.email_addr} {emailConf.email_passwd} {emailConf.server_uri} {emailConf.smtp_server_uri}'
            msg += f"    {state} `{addCommand}`\n"
    # safeSendText(
    #     lambda text: update.message.reply_markdown_v2(text), # type: ignore[has-type]
    #     msg
    # )
    boxes = asyncio.run(telegramify_markdown.telegramify(
        msg, 
        interpreters_use=telegramify_markdown.InterpreterChain([
            telegramify_markdown.TextInterpreter(),
        ])
    ))
    for i, box in enumerate(boxes):
        assert box.content_type == telegramify_markdown.ContentTypes.TEXT
        content = box.content
        if not content:
            continue
        if len(boxes) - 1 != i:
            content += '\n' + r'_\(\.\.\.\)_'
        safeSend(
            lambda text: update.message.reply_markdown_v2(text),
            content
        )

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

    oauthProvider = OAuth2Factory.get_provider(email_addr, oauth_type)
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
    email_server = None
    if len(context.args) > 2:
        email_server = context.args[2]
    email_smtp = None
    if len(context.args) > 3:
        email_smtp = context.args[3]
    
    if email_server is None:
        ret = get_mail_server(email_addr)
        if not ret:
            update.message.reply_text(f"cannot auto detect mail server for {email_server}, please manually specify server address")
            return
        email_server, email_smtp = ret
    
    if not email_server.startswith('imap') and not email_server.startswith('pop3') and not email_server.startswith('proton'):
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
        mailbox_offsets={},
    )
    
    logger.info("received setting_email command.")
    
    new_passwd = OAuth2Factory.code_to_token(email_addr, email_passwd)
    if new_passwd:
        update.message.reply_text(f"Exchanged refresh_token {new_passwd} from {email_passwd} for email {email_addr}, Rewriting password~")
        emailConf.email_passwd = email_passwd = new_passwd

    with getEmailClient(emailConf) as client:
        # initialize mailbox offsets
        try:
            mailboxes = client.get_mailboxes()
        except Exception as e:
            logger.warning("Failed to list mailboxes: %s", e)
            mailboxes = ['inbox']

        try:
            countmap = client.get_mails_countmap(mailboxes)
        except Exception as e:
            logger.warning("Failed to get countmap: %s", e)
            countmap = {}
            
        for mb in mailboxes:
            if mb in countmap:
                emailConf.mailbox_offsets[mb] = countmap[mb]
            else:
                logger.warning("Failed to get count for mailbox %s", mb)
                emailConf.mailbox_offsets[mb] = 0
    
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

def setting_disable_email(update: Update, context: CallbackContext) -> None:
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
    emailDB.updateById(pk, {'disabled': True})
    update.message.reply_text(f'Successfully disabled email account {email_addr}')

def setting_enable_email(update: Update, context: CallbackContext) -> None:
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
    emailDB.updateById(pk, {'disabled': False})
    update.message.reply_text(f'Successfully re-enabled email account {email_addr}')

def safeSendText(sender, content):
    for text in handle_large_text(content):
        safeSend(sender, text)

def safeSend(sender, content):
    from telegram.error import RetryAfter
    for i in range(10):
        try:
            sender(content)
            break
        except RetryAfter as e:
            retry_after = e.retry_after + 1
            logger.warning('Telegram flood control: retry after %d seconds (attempt %d)', retry_after, i)
            time.sleep(retry_after)
        except Exception:
            logger.warning('cannot send tg msg (retry %d)\n%s', i, content, exc_info=True)
            time.sleep(i * 5)

emailClientCache: dict[tuple, EmailClientBase] = {}
def getEmailClient(emailConf: EmailConf) -> EmailClientBase:
    baseConf = EmailConfBase.from_dict(emailConf.as_dict(), ignore_extra_fields=True)
    cacheKey = dataclasses.astuple(baseConf)
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
    elif emailConf.server_uri.startswith('proton'):
        EmailClient = EmailClientProton
    else:
        raise Exception(f"invalid email server_uri: {emailConf.server_uri}")
    
    emailClient = EmailClient(emailConf.email_addr, emailConf.email_passwd, emailConf.server_uri)
    emailClientCache[cacheKey] = emailClient
    return emailClient

def getAllPlugins():
    for plugin_name in Conf.ENABLED_PLUGINS.split(','):
        if not plugin_name:
            continue
        try:
            plugin = importlib.import_module(f'plugins.{plugin_name}')
        except:
            logger.info('cannot import plugin %s:', plugin_name, exc_info=True)
            continue
        yield plugin_name, plugin.PLUGIN

PERIODIC_TASK_ERRORS: dict[str, dict[str, list[str]]] = {
    
}
PERIODIC_TASK_TICK = 0
periodicThreadPool = ThreadPool(Conf.POLL_THREADS)
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
            emailConf = EmailConf.from_dict(emailConfDict)
            email_addr = emailConf.email_addr
        except Exception:
            logger.warning('Cannot parse emailConfDict: %s', emailConfDict, exc_info=True)
            return
        if emailConf.disabled:
            # logger.debug("email account %s is disabled, skip", email_addr)
            return
        # from multiprocessing import get_context, Process, Manager, Queue
        # ctx = get_context('fork')
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
            logger.info("running %s with timeout %d", fun.__name__, Conf.REQUEST_TIMEOUT)
            pool = ThreadPool(1)
            fut = pool.apply_async(fun, args=args, kwds=kwargs)
            return fut.get(Conf.REQUEST_TIMEOUT)
        try:
            def do():
                logger.debug("[%s] connecting email client", email_addr)
                client = getEmailClient(emailConf)
                chat_id, reply_to_message_id = emailConf.chat_id.split(',') if ',' in emailConf.chat_id else (emailConf.chat_id, None)
                
                def iter_new_mails(client, emailConf):
                    try:
                        mailboxes = client.get_mailboxes()
                    except Exception:
                        logger.warning("[%s] failed to list mailboxes", email_addr, exc_info=True)
                        return

                    try:
                        countmap = client.get_mails_countmap(mailboxes)
                    except Exception:
                        logger.warning("[%s] failed to get countmap: %s", email_addr, exc_info=True)
                        return

                    for mailbox in mailboxes:
                        cur_inbox_num = emailConf.mailbox_offsets.get(mailbox, -1)
                        if mailbox not in countmap:
                             logger.warning("[%s] failed to get count for %s", email_addr, mailbox)
                             continue
                        new_inbox_num = countmap[mailbox]

                        logger.debug("[%s] box: %s, cur: %d, new: %d", email_addr, mailbox, cur_inbox_num, new_inbox_num)
                        
                        if cur_inbox_num == -1:
                            emailConf.mailbox_offsets[mailbox] = new_inbox_num
                            emailDB.updateByQuery({'email_addr': email_addr}, {'mailbox_offsets': emailConf.mailbox_offsets})
                            continue

                        if new_inbox_num < cur_inbox_num:
                            emailConf.mailbox_offsets[mailbox] = new_inbox_num
                            emailDB.updateByQuery({'email_addr': email_addr}, {'mailbox_offsets': emailConf.mailbox_offsets})
                        elif new_inbox_num > cur_inbox_num:
                            for idx in range(cur_inbox_num + 1, new_inbox_num + 1):
                                try:
                                    mail = client.get_mail_by_index(idx, mailbox)
                                except Exception:
                                    logger.warning("[%s] cannot retrieve mail %d for %s", email_addr, idx, mailbox, exc_info=True)
                                    break
                                yield mailbox, idx, mail

                seen_ids = {}
                for mailbox, idx, mail in iter_new_mails(client, emailConf):
                    logstr = f'[{email_addr}]["{mailbox}"]'
                    if mail.id and mail.id in seen_ids:
                        logger.info(f'{logstr} Duplicate email found: %s (in both %s and %s), skipping...', mail.id, mailbox, seen_ids[mail.id])
                        emailConf.mailbox_offsets[mailbox] = idx
                        emailDB.updateByQuery({'email_addr': email_addr}, {'mailbox_offsets': emailConf.mailbox_offsets})
                        continue
                    if mail.id:
                        seen_ids[mail.id] = mailbox
                    
                    if True:
                        logger.info(f'{logstr} Got new email: %s', mail.msg_content)
                        if Conf.SAVE_EMAIL_LOGS:
                            emlFileName= f'logs/{email_addr}/{idx}.eml'
                            os.makedirs(os.path.dirname(emlFileName), exist_ok=True)
                            with open(emlFileName, 'wb') as f:
                                if isinstance(mail.msg_content, str):
                                    f.write(mail.msg_content.encode())
                                else:
                                    f.write(mail.msg_content)
                        
                        text = f'''New Email [{emailConf.email_addr}-{idx}]\n'''
                        if mailbox.lower() != 'inbox':
                            text = f'''New Email [{emailConf.email_addr}][{mailbox}]\n'''
                            
                        emailbody, emailfiles = mail.format_email(prefer_html=Conf.PREFER_HTML)
                        text += emailbody
                        
                        interceptMail = False
                        for plugin_name, plugin in getAllPlugins():
                            plugin: PluginBase
                            ret = plugin.onNewEmail(email_addr, mail)
                            if ret:
                                logger.info(f'{logstr} Email message intercepted by plugin {plugin_name}')
                                interceptMail = True

                        if not interceptMail:
                            md = text
                            boxes = asyncio.run(telegramify_markdown.telegramify(
                                md, 
                                interpreters_use=telegramify_markdown.InterpreterChain([
                                    telegramify_markdown.TextInterpreter(),
                                ])
                            ))
                            for i, box in enumerate(boxes):
                                assert box.content_type == telegramify_markdown.ContentTypes.TEXT
                                content = box.content
                                if len(boxes) - 1 != i:
                                    content += '\n' + r'_\(\.\.\.\)_'
                                safeSend(
                                    lambda text: updater.bot.send_message(
                                        chat_id=chat_id,reply_to_message_id=reply_to_message_id,
                                        text=text,
                                        parse_mode="MarkdownV2"), # type: ignore[has-type]
                                    content
                                )
                            for filename, filemime, file_content in emailfiles:
                                if filemime.startswith('image'):
                                    safeSend(
                                        lambda text: updater.bot.send_photo(chat_id=chat_id, reply_to_message_id=reply_to_message_id, photo=file_content, filename=filename), # type: ignore[has-type]
                                        text
                                    )
                                else:
                                    safeSend(
                                        lambda text: updater.bot.send_document(chat_id=chat_id, reply_to_message_id=reply_to_message_id, document=file_content, filename=filename), # type: ignore[has-type]
                                        text
                                    )
                        else:
                            logger.info(f'{logstr} Not sending intercepted email message: %s', text)
                        emailConf.mailbox_offsets[mailbox] = idx
                        emailDB.updateByQuery({'email_addr': email_addr}, {'mailbox_offsets': emailConf.mailbox_offsets})
            run_with_timeout(do)
        except Exception as e:
            if re.findall(r'\bEOF\b', str(e)):
                pass # do not process occasional random network issue
            elif re.findall(r'Server Unavailable. 21', str(e)):
                pass # ignore stupid outlook server error
            else:
                if email_addr not in PERIODIC_TASK_ERRORS:
                    PERIODIC_TASK_ERRORS[email_addr] = {}
                exceptionStr = str(e)
                # for exceptions like "server returned invalid status %d, body: %s", we merge them into one category
                if ':' in exceptionStr[:100]:
                    exceptionStr = exceptionStr[:100].split(':')[0]
                if exceptionStr not in PERIODIC_TASK_ERRORS[email_addr]:
                    PERIODIC_TASK_ERRORS[email_addr][exceptionStr] = []
                PERIODIC_TASK_ERRORS[email_addr][exceptionStr].append((f'{type(e).__name__}: {str(e)}', format_exc()))
            logger.warning('periodic task error in %s', email_addr, exc_info=True)
    
    # for emailConfDict in emailDB.getAll():
    #     handler(emailConfDict)
    
    # TODO: Implement timeout control
    for _ in periodicThreadPool.imap_unordered(handler, emailDB.getAll()):
        pass

LAST_ERROR_REPORT_TIME: float | None = None
LAST_ERROR_REPORT_TICK = 0
def periodic_task_error_report():
    try:
        global PERIODIC_TASK_ERRORS, LAST_ERROR_REPORT_TIME, LAST_ERROR_REPORT_TICK
        
        last_errors = PERIODIC_TASK_ERRORS
        queries = PERIODIC_TASK_TICK - LAST_ERROR_REPORT_TICK
        
        last_errors_simplified = {k: {k1: '(%d errors)' % len(v1) for k1,v1 in v.items()} for k,v in last_errors.items()}
        logger.info("last errors (%s - %s, %d queries): %s", LAST_ERROR_REPORT_TIME, time.time(), queries, last_errors_simplified)

        seconds_since_last_report = Conf.ERR_REPORT_INTERVAL
        if LAST_ERROR_REPORT_TIME is not None:
            seconds_since_last_report = int(time.time() - LAST_ERROR_REPORT_TIME)
        time_since_last_report = str(datetime.timedelta(seconds=seconds_since_last_report))

        LAST_ERROR_REPORT_TICK = PERIODIC_TASK_TICK
        LAST_ERROR_REPORT_TIME = time.time()
        PERIODIC_TASK_ERRORS = {}
        
        if not last_errors:
            logger.info("No errors since last error report, skipping this report!")
            return
        
        text = ''
        for acc, accErrDict in last_errors.items():
            accTotalErrs = sum(len(errList) for errList in accErrDict.values())
            if accTotalErrs > queries * 0.5:
                # to avoid spamming, we only print error summary if this account has MASSIVE amount of errors
                text += f'\nAcc: {acc}\n'
                for errType, errList in accErrDict.items():
                    text += f'    Error: {errList[0][0]}, triggered {len(errList)} times\n'
            else:
                logger.info("Acc %s's err is occasional (%d vs %d), ignoring", acc, accTotalErrs, queries)
        if not text:
            logger.info("No account have massive errors, skipping this report!")
            return
        logger.info("Error summary this round: %s", text)
        text = f'''Error Summary during last {queries} queries in duration {time_since_last_report}:\n''' + text
        safeSendText(
            lambda text: updater.bot.send_message(chat_id=Conf.OWNER_CHAT_ID, text=text), # type: ignore[has-type]
            text
        )
    except Exception:
        logger.error('Error in periodic_task_error_report:', exc_info=True)

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
    email, mail_id, from_name, from_email, email_id = re.findall(
        r"""
        ^.*?\[(?P<email>.*?)-(?P<email_no>\d+)\]\n # New Email [abc@a.com-1234]
        [\S\s]+?                                   # subjects, etc.
        From:\s(?P<from_name>.*?)<*(?P<from_email>\S+)>*\n                            # `From: xxx a@b.com` or `From: xxx <...>`
        [\S\s]+?                                   # date, etc.
        ID:\s(?P<email_id>.*?)\S*\n                # ID: <...>
        """, original_message, flags=re.VERBOSE)[0]
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
        subject=subject, body=body,
        reply_email_id=email_id)
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
    dp.add_handler(CommandHandler("disable_email", setting_disable_email))
    dp.add_handler(CommandHandler("enable_email", setting_enable_email))
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
    
    for plugin_name, plugin in getAllPlugins():
        plugin: PluginBase
        logger.info('Initializing plugin %s', plugin_name)
        plugin.onSetup(updater)

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
