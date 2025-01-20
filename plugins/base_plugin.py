from utils.mail import Email

class PluginBase:
    def onSetup(self, updater):
        return
    
    def onNewEmail(self, email_addr, email: Email) -> bool:
        return False

PLUGIN = PluginBase()