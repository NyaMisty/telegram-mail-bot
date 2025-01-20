import logging

from plugins.base_plugin import PluginBase
from utils.mail import Email
logger = logging.getLogger(__name__)

class PluginExample(PluginBase):
    def __init__(self):
        pass
    
    def onSetup(self, updater):
        logger.info('plugin onStart called! updater=%s', updater)
    
    def onNewEmail(self, email_addr, email: Email) -> bool:
        logger.info('plugin onNewEmail called!')
        if email.subject == '[[BLACKLIST EMAIL]]':
            return True # return true to not forwarding email to chat
        return False

PLUGIN = PluginExample()