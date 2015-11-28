from ws import models


def chair_group(activity):
    if activity == 'winter_school':
        return 'WSC'
    return activity + '_chair'

def in_any_group(user, group_names, allow_superusers=True):
    if user.is_authenticated():
        sudo = allow_superusers and user.is_superuser
        if bool(user.groups.filter(name__in=group_names)) or sudo:
            return True

def is_chair(user, activity_type, allow_superusers=True):
    return in_any_group(user, [chair_group(activity_type)], allow_superusers)


activity_types = [val for val, label in models.LeaderRating.ACTIVITIES]
all_chair_groups = {chair_group(activity) for activity in activity_types}


def chair_activities(user):
    """ All activities for which the user is the chair. """
    return [activity for activity in activity_types if is_chair(user, activity)]