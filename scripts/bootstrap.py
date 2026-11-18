import os
from django.contrib.auth import get_user_model
from freppledb import __version__
from django.conf import settings

User = get_user_model()
user, _ = User.objects.get_or_create(username=os.environ['P0_ENGINE_USER'])
user.is_active = True
user.is_staff = True
user.is_superuser = True
user.set_password(os.environ['P0_ENGINE_PASSWORD'])
user.save()
User.objects.filter(username='admin').update(is_active=False)
print('P0_BOOTSTRAP', {'engine_version': __version__, 'timezone': settings.TIME_ZONE})
