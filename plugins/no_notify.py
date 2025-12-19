import os
import logging

from plugins.base_plugin import PluginBase
from utils.mail import Email
logger = logging.getLogger(__name__)

class PluginNoNotify(PluginBase):
    def __init__(self):
        self.no_notify_list = []
    
    def onSetup(self, updater):
        self.no_notify_list = os.getenv('NO_NOTIFY_LIST', '').split(',')
        self.no_notify_list = [c.lower() for c in self.no_notify_list]
        logger.info('plugin %s onStart called! no notify email list: %s', self.__class__.__name__, self.no_notify_list)
    
    def onNewEmail(self, email_addr, email: Email) -> bool:
        logger.info('plugin onNewEmail called!')
        if email_addr.lower() in self.no_notify_list:
            return True # return true to not forwarding email to chat
        return False

PLUGIN = PluginNoNotify()