""" Production settings """

import os

DEBUG = False
TEMPLATE_DEBUG = False
ALLOWED_HOSTS = ['trips.mitoc.edu', 'mitoc-trips.mit.edu']

ADMINS = (('David Cain', 'davidjosephcain@gmail.com'),)

SERVER_EMAIL = 'no-reply@mitoc.org'
DEFAULT_FROM_EMAIL = 'mitoc-trips@mit.edu'

ACCOUNT_EMAIL_VERIFICATION = 'mandatory'

EMAIL_BACKEND = 'django_smtp_ssl.SSLEmailBackend'
EMAIL_HOST = 'email-smtp.us-east-1.amazonaws.com'
EMAIL_PORT = 465
EMAIL_HOST_USER = os.getenv('SES_USER')
EMAIL_HOST_PASSWORD = os.getenv('SES_PASSWORD')
