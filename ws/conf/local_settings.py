"""
Local settings, intended for use on a local computer for basic
feature testing.

  - Exposed secret key
  - DEBUG enabled
  - Project root is just '/'
  - Send emails to console
"""

DEBUG = True
ALLOWED_HOSTS = ['*']

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Don't send emails to verify email addresses (rapid account creation)
ACCOUNT_EMAIL_VERIFICATION = 'none'


DEBUG_TOOLBAR_PATCH_SETTINGS = False
INTERNAL_IPS = ['127.0.0.1', '192.168.33.15']
