from datetime import datetime, timedelta
import io
import logging
from pathlib import Path
import random

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q

from ws import models
from ws.utils.dates import local_now, local_date, closest_wed_at_noon, jan_1
from ws.utils.signups import add_to_waitlist
from ws import settings


def reciprocally_paired(participant):
    try:
        lotteryinfo = participant.lotteryinfo
    except models.LotteryInfo.DoesNotExist:
        return False
    return bool(lotteryinfo.reciprocally_paired_with)


def par_is_driver(participant):
    try:
        return participant.lotteryinfo.is_driver
    except AttributeError:  # No lottery form submission
        return False


def place_on_trip(signup, logger):
    trip = signup.trip
    slots = ' slot' if trip.open_slots == 1 else 'slots'
    logger.info(f"{trip} has {trip.open_slots} {slots}, adding {signup.participant}")
    signup.on_trip = True
    signup.save()


def affiliation_weighted_rand(participant):
    """ Return a float that's meant to rank participants by affiliation.

    A lower number is a "preferable" affiliation. That is to say, ranking
    participants by the result of this function will put MIT students towards
    the beginning of the list more often than not.
    """
    weights = {
        'MU': 0.3, 'MG': 0.2, 'MA': 0.1,  # (MIT undergrads, grads, affiliates)
        'NU': 0.0, 'NG': 0.0, 'NA': 0.0,  # (non-MIT students, general public)
        # Old, deprecated status codes
        'M': 0.1, 'N': 0.0, 'S': 0.0
    }
    return random.random() - weights[participant.affiliation]


class WinterSchoolParticipantRanker:
    def __init__(self):
        self.today = local_date()
        self.jan_1st = jan_1()

    def __iter__(self):
        """ Ordered list of participants, ranked by:

        1. number of trips (fewer -> higher priority)
        2. affiliation (MIT affiliated is higher priority)
        3. 'flakiness' (more flakes -> lower priority
        """
        participants = models.Participant.objects.all()
        return iter(sorted(participants, key=self.priority_key))

    def priority_key(self, participant):
        """ Return tuple for sorting participants. """
        flake_factor = self.flake_factor(participant)
        # If we use raw flake factor, participants who've been on trips
        # will have an advantage over those who've been on none
        flaky_or_neutral = max(flake_factor, 0)

        # If the leader led more trips, give them a bump
        leader_bump = -self.trips_led_balance(participant)

        # Ties are resolved by a random number
        # (MIT students/affiliates are more likely to come first)
        affiliation_weight = affiliation_weighted_rand(participant)

        # Lower = higher in the list
        return (flaky_or_neutral, leader_bump, affiliation_weight)

    def flake_factor(self, participant):
        """ Return a number indicating past "flakiness".

        A lower score indicates a more reliable participant.
        """
        score = 0
        for trip in self.past_ws_trips(participant):
            trip_feedback = participant.feedback_set.filter(trip=trip)
            if not trip_feedback.exists():
                continue
            # If any leader says they flaked, then assume a flake
            showed_up = all(feedback.showed_up for feedback in trip_feedback)
            score += 5 if not showed_up else -2
        return score

    def past_ws_trips(self, participant):
        """ Past Winter School trips participant has been on this year. """
        return participant.trip_set.filter(trip_date__gt=self.jan_1st,
                                           trip_date__lt=self.today,
                                           activity='winter_school')

    def number_trips_led(self, participant):
        """ Return the number of trips the participant has recently led.

        (If we considered all the trips the participant has ever led,
        participants could easily jump the queue every Winter School if they
        just lead a few trips once and then stop).
        """
        last_year = local_date() - timedelta(days=365)
        return participant.trips_led.filter(trip_date__gt=last_year).count()

    def number_ws_trips(self, participant):
        past_trips = self.past_ws_trips(participant)
        signups = participant.signup_set.filter(trip__in=past_trips, on_trip=True)
        return signups.count()

    def trips_led_balance(self, par):
        """ Especially active leaders get priority. """
        surplus = self.number_trips_led(par) - self.number_ws_trips(par)
        return max(surplus, 0)  # Don't penalize anybody for a negative balance

    def lowest_non_driver(self, trip):
        """ Return the lowest priority non-driver on the trip. """
        no_car = (Q(participant__lotteryinfo__isnull=True) |
                  Q(participant__lotteryinfo__car_status='none'))
        non_drivers = trip.signup_set.filter(no_car, on_trip=True)
        return max(non_drivers, key=lambda signup: self.priority_key(signup.participant))


class LotteryRunner:
    def __init__(self):
        # Get a logger instance that captures activity for _just_ this run
        self.logger = logging.getLogger(self.logger_id)
        self.logger.setLevel(logging.DEBUG)

        self.participants_handled = {}  # Key: primary keys, gives boolean if handled

    @property
    def logger_id(self):
        """ Get a unique logger object per each instance. """
        return f"{__name__}.{id(self)}"

    def handled(self, participant):
        return self.participants_handled.get(participant.pk, False)

    def mark_handled(self, participant, handled=True):
        self.participants_handled[participant.pk] = handled

    def participant_to_bump(self, trip):
        """ Which participant to bump off the trip if another needs a place.

        By default, just goes with the most recently-added participant.
        Standard us case: Somebody needs to be bumped so a driver may join.
        """
        on_trip = trip.signup_set.filter(on_trip=True)
        return on_trip.order_by('-last_updated').first()

    def __call__(self):
        raise NotImplementedError("Subclasses must implement lottery behavior")


class SingleTripLotteryRunner(LotteryRunner):
    def __init__(self, trip):
        self.trip = trip
        super().__init__()
        self.configure_logger()

    @property
    def logger_id(self):
        """ Get a constant logger identifier for each trip. """
        return f"{__name__}.trip.{self.trip.pk}"

    def configure_logger(self):
        """ Configure a stream to save the log to the trip. """
        self.log_stream = io.StringIO()

        self.handler = logging.StreamHandler(stream=self.log_stream)
        self.handler.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)

    def ranked_participants(self):
        participants = (s.participant for s in self.trip.signup_set.all())
        return sorted(participants, key=affiliation_weighted_rand)

    def __call__(self):
        if self.trip.algorithm != 'lottery':
            self.log_stream.close()
            return

        self.logger.info("Randomly ordering (preference to MIT affiliates)...")
        ranked_participants = self.ranked_participants()
        self.logger.info("Participants will be handled in the following order:")
        max_len = max(len(par.name) for par in ranked_participants)
        for i, par in enumerate(ranked_participants, start=1):
            affiliation = par.get_affiliation_display()
            self.logger.info(f"{i:3}. {par.name:{max_len + 3}} ({affiliation})")

        self.logger.info(50 * '-')
        for participant in ranked_participants:
            par_handler = SingleTripParticipantHandler(participant, self, self.trip)
            par_handler.place_participant()

        self.trip.algorithm = 'fcfs'
        self.trip.lottery_log = self.log_stream.getvalue()
        self.log_stream.close()
        self.trip.save()


class WinterSchoolLotteryRunner(LotteryRunner):
    def __init__(self):
        self.ranked_participants = WinterSchoolParticipantRanker()
        super().__init__()
        self.configure_logger()

    def configure_logger(self):
        """ Configure a stream to save the log to the trip. """
        datestring = datetime.strftime(local_now(), "%Y-%m-%dT:%H:%M:%S")
        filename = Path(settings.WS_LOTTERY_LOG_DIR, f"ws_{datestring}.log")
        self.handler = logging.FileHandler(filename)
        self.handler.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)

    def __call__(self):
        self.assign_trips()
        self.free_for_all()
        self.handler.close()

    def free_for_all(self):
        """ Make trips first-come, first-serve.

        Trips re-open Wednesday at noon, close at midnight on Thursday.
        """
        self.logger.info("Making all lottery trips first-come, first-serve")
        ws_trips = models.Trip.objects.filter(activity='winter_school')
        noon = closest_wed_at_noon()
        for trip in ws_trips.filter(algorithm='lottery'):
            trip.make_fcfs(signups_open_at=noon)
            trip.save()

    def participant_to_bump(self, trip):
        return self.ranked_participants.lowest_non_driver(trip)

    def assign_trips(self):
        for participant in self.ranked_participants:
            handling_text = f"Handling {participant}"
            self.logger.debug(handling_text)
            self.logger.debug('-' * len(handling_text))
            par_handler = WinterSchoolParticipantHandler(participant, self)
            par_handler.place_participant()


class ParticipantHandler:
    """ Class to handle placement of a single participant or pair. """
    is_driver_q = Q(participant__lotteryinfo__car_status__in=['own', 'rent'])

    def __init__(self, participant, runner, min_drivers=2, allow_pairs=True):
        self.participant = participant
        self.runner = runner
        self.min_drivers = min_drivers
        self.allow_pairs = allow_pairs

        self.slots_needed = len(self.to_be_placed)

    @property
    def logger(self):
        """ All logging is routed through the runner's logger. """
        return self.runner.logger

    @property
    def is_driver(self):
        return any(par_is_driver(par) for par in self.to_be_placed)

    @property
    def paired(self):
        return reciprocally_paired(self.participant)

    @property
    def paired_par(self):
        try:
            return self.participant.lotteryinfo.paired_with
        except ObjectDoesNotExist:
            return None

    @property
    def to_be_placed(self):
        if self.paired and self.allow_pairs:
            return (self.participant, self.paired_par)
        else:
            return (self.participant,)

    @property
    def par_text(self):
        return " + ".join(map(str, self.to_be_placed))

    def place_all_on_trip(self, signup):
        place_on_trip(signup, self.logger)
        if self.paired:
            par_signup = models.SignUp.objects.get(participant=self.paired_par,
                                                   trip=signup.trip)
            place_on_trip(par_signup, self.logger)

    def count_drivers_on_trip(self, trip):
        participant_drivers = trip.signup_set.filter(self.is_driver_q, on_trip=True)
        lottery_leaders = trip.leaders.filter(lotteryinfo__isnull=False)
        num_leader_drivers = sum(leader.lotteryinfo.is_driver
                                 for leader in lottery_leaders)
        return participant_drivers.count() + num_leader_drivers

    def try_to_place(self, signup):
        """ Try to place participant (and partner) on the trip.

        Returns if successful.
        """
        trip = signup.trip
        if trip.open_slots >= self.slots_needed:
            self.place_all_on_trip(signup)
            return True
        elif self.is_driver and not trip.open_slots and not self.paired:
            # A driver may displace somebody else
            # (but a couple with a driver cannot displace two people)
            if self.count_drivers_on_trip(trip) < self.min_drivers:
                self.logger.info(f"{trip} is full, but doesn't have {self.min_drivers} drivers")
                self.logger.info(f"Adding {signup} to '{trip}', as they're a driver")
                par_to_bump = self.runner.participant_to_bump(trip)
                add_to_waitlist(par_to_bump, prioritize=True)
                self.logger.info(f"Moved {par_to_bump} to the top of the waitlist")
                # TODO: Try to move the bumped participant to a preferred, open trip!
                signup.on_trip = True
                signup.save()
                return True
        return False

    def place_participant(self):
        raise NotImplementedError()
        # (Place on trip or waitlist, then):
        #self.runner.mark_handled(self.participant)


class SingleTripParticipantHandler(ParticipantHandler):
    def __init__(self, participant, runner, trip):
        self.trip = trip
        allow_pairs = trip.honor_participant_pairing
        # TODO: Minimum driver requirements should be supported
        return super().__init__(participant, runner, allow_pairs=allow_pairs, min_drivers=0)

    @property
    def paired(self):
        # Obviously, participants must mark each other as in the pair
        if not reciprocally_paired(self.participant):
            return False

        # A participant is only paired if both signed up for this trip
        partner_signed_up = Q(trip=self.trip, participant=self.paired_par)
        return models.SignUp.objects.filter(partner_signed_up).exists()

    def place_participant(self):
        if self.paired:
            self.logger.info(f"{self.participant} is paired with {self.paired_par}")
            if not self.runner.handled(self.paired_par):
                self.logger.info(f"Will handle signups when {self.paired_par} comes")
                self.runner.mark_handled(self.participant)
                return

        # Try to place all participants, otherwise add them to the waitlist
        signup = models.SignUp.objects.get(participant=self.participant,
                                           trip=self.trip)
        if not self.try_to_place(signup):
            for par in self.to_be_placed:
                self.logger.info(f"Adding {par.name} to the waitlist")
                add_to_waitlist(models.SignUp.objects.get(trip=self.trip,
                                                          participant=par))
        self.runner.mark_handled(self.participant)


class WinterSchoolParticipantHandler(ParticipantHandler):
    def __init__(self, participant, runner):
        """
        :param runner: An instance of LotteryRunner
        """
        self.today = local_date()
        return super().__init__(participant, runner, min_drivers=2, allow_pairs=True)

    @property
    def future_signups(self):
        # Only consider lottery signups for future trips
        signups = self.participant.signup_set.filter(
            trip__trip_date__gt=self.today,
            trip__algorithm='lottery',
            trip__activity='winter_school'
        )
        if self.paired:  # Restrict signups to those both signed up for
            signups = signups.filter(trip__in=self.paired_par.trip_set.all())
        return signups.order_by('order', 'time_created')

    def place_participant(self):
        if self.paired:
            self.logger.info(f"{self.participant} is paired with {self.paired_par}")
            if not self.runner.handled(self.paired_par):
                self.logger.info(f"Will handle signups when {self.paired_par} comes")
                self.runner.mark_handled(self.participant)
                return
        if not self.future_signups:
            self.logger.info(f"{self.par_text} did not choose any trips this week")
            self.runner.mark_handled(self.participant)
            return

        # Try to place participants on their first choice available trip
        for signup in self.future_signups:
            if self.try_to_place(signup):
                break
            else:
                self.logger.info(f"Can't place {self.par_text} on {signup.trip}")

        else:  # No trips are open
            self.logger.info(f"None of {self.par_text}'s trips are open.")
            favorite_trip = self.future_signups.first().trip
            for participant in self.to_be_placed:
                find_signup = Q(participant=participant, trip=favorite_trip)
                favorite_signup = models.SignUp.objects.get(find_signup)
                add_to_waitlist(favorite_signup)
                self.logger.info(f"Waitlisted {self.par_text} on {favorite_signup.trip.name}")

        self.runner.mark_handled(self.participant)
