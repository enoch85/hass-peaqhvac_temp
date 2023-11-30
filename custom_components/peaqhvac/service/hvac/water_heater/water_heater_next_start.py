from datetime import datetime, timedelta
import logging

from custom_components.peaqhvac.service.hvac.water_heater.models.group import Group
from custom_components.peaqhvac.service.hvac.water_heater.models.next_water_boost_model import NextWaterBoostModel, DEMAND_MINUTES, get_demand
from custom_components.peaqhvac.service.models.enums.group_type import GroupType
from custom_components.peaqhvac.service.models.enums.demand import Demand
from custom_components.peaqhvac.service.models.enums.hvac_presets import HvacPresets



_LOGGER = logging.getLogger(__name__)


class NextWaterBoost:
    def __init__(self, min_price: float = None, non_hours: list[int] = None, demand_hours: list[int] = None):
        self.model = NextWaterBoostModel(min_price=min_price, non_hours_raw=non_hours, demand_hours_raw=demand_hours)
    def next_predicted_demand(
            self,
            prices_today: list,
            prices_tomorrow: list,
            temp: float,
            temp_trend: float,
            target_temp: float,
            preset: HvacPresets = HvacPresets.Normal,
            now_dt=None,
            latest_boost: datetime = None,
    ) -> tuple[datetime, int | None]:
        if len(prices_today) < 1:
            return datetime.max, None
        self.model.init_vars(temp, temp_trend, target_temp, prices_today, prices_tomorrow, preset, now_dt, latest_boost)

        next_start = self.model.latest_calculation
        if self.model.should_update:
            next_start = self._get_next_start(
                delay_dt=None if self.model.cold_limit == now_dt else self.model.cold_limit
            )
            self.model.latest_calculation = next_start
            self.model.should_update = False
        return next_start

    def _get_next_start(self, delay_dt=None) -> tuple[datetime, int | None]:
        last_known = self._last_known_price()
        # check_dt: datetime = last_known
        # if delay_dt:
        #     check_dt = min(delay_dt, last_known)

        latest_limit = self.model.latest_boost + timedelta(hours=24) if self.model.latest_boost else datetime.now()
        if latest_limit < self.model.now_dt and self.model.is_cold:
            """It's been too long since last boost. Boost now."""
            return self.model.now_dt.replace(
                minute=self._set_minute_start(now_dt=datetime.now()),
                second=0,
                microsecond=0
            ), None

        next_dt = self._calculate_next_start(delay_dt)  # todo: must also use latestboost +24h in this.

        intersecting1 = self._check_intersecting(next_dt, last_known)
        if intersecting1:
            return intersecting1

        expected_temp = min(self._get_temperature_at_datetime(next_dt), 39)
        return self._set_start_dt(
            low_period=0,
            delayed_dt=next_dt,
            new_demand=self.model.get_demand_minutes(expected_temp)
        ), None

    def _check_intersecting(self, next_dt, last_known) -> tuple[datetime, int | None]:
        intersecting_non_hours = self._intersecting_special_hours(self.model.non_hours, min(next_dt, last_known))
        intersecting_demand_hours = self._intersecting_special_hours(self.model.demand_hours, min(next_dt, last_known))
        if intersecting_demand_hours:
            best_match = self._get_best_match(intersecting_non_hours, intersecting_demand_hours)
            if best_match:
                # print(f"best match: {best_match}")
                expected_temp = min(self._get_temperature_at_datetime(best_match), 39)
                ret = self._set_start_dt(
                    low_period=0,
                    delayed_dt=min(best_match, next_dt),
                    new_demand=self.model.get_demand_minutes(expected_temp)
                    # special demand because demand_hour
                ), self.model.get_demand_minutes(expected_temp)
                if ret[0] - self.model.latest_boost > timedelta(hours=1):
                    return ret
        return None

    @staticmethod
    def _get_best_match(non_hours, demand_hours) -> datetime | None:
        first_demand = min(demand_hours)
        non_hours = [hour.hour for hour in non_hours]
        for hour in range(first_demand.hour - 1, -1, -1):
            if hour not in non_hours:
                if hour - 1 not in non_hours:
                    return first_demand.replace(hour=hour - 1)
        return None

    def _intersecting_special_hours(self, hourslist, next_dt) -> list[datetime]:
        hours_til_boost = self._get_list_of_hours(self.model.now_dt, next_dt)
        intersection = hours_til_boost.intersection(hourslist)
        if not len(intersection):
            return []
        return list(sorted(intersection))

    def _get_temperature_at_datetime(self, target_dt) -> float:
        delay = (target_dt - self.model.now_dt).total_seconds() / 3600
        return self.model.current_temp + (delay * self.model.temp_trend)

    def _get_list_of_hours(self, start_dt: datetime, end_dt: datetime) -> set[datetime]:
        hours = []
        current = start_dt
        end_dt += timedelta(hours=1)  # Include the hour after end_dt
        while current <= end_dt:
            hours.append(current.replace(minute=0, second=0, microsecond=0))
            current += timedelta(hours=1)
        return set(hours)

    def _last_known_price(self) -> datetime:
        try:
            last_known_price = self.model.now_dt.replace(hour=0, minute=0, second=0) + timedelta(
                hours=len(self.model.prices) - 1)
            return last_known_price
        except Exception as e:
            _LOGGER.error(
                f"Error on getting last known price with {self.model.now_dt} and len prices {len(self.model.prices)}: {e}")
            return datetime.max

    def _set_minute_start(self, now_dt, low_period=0, delayed=False, new_demand: int = None) -> int:
        demand = new_demand if new_demand is not None else self.model.demand_minutes
        if low_period >= 60 - now_dt.minute and not delayed:
            start_minute = max(now_dt.minute, min(60 - int(demand / 2), 59))
        else:
            start_minute = min(60 - int(demand / 2), 59)
        return start_minute

    def _set_start_dt(self, low_period: int, delayed_dt: datetime = None, delayed: bool = False,
                      new_demand: int = None) -> datetime:
        now_dt = self.model.now_dt if delayed_dt is None else delayed_dt
        start_minute: int = self._set_minute_start(now_dt, low_period, delayed, new_demand)
        return now_dt.replace(minute=start_minute, second=0, microsecond=0)

    def _get_low_period(self, override_dt=None) -> int:
        dt = self.model.now_dt if override_dt is None else override_dt
        if override_dt is not None:
            _start_hour = dt.hour + (int(self.model.now_dt.day != override_dt.day) * 24)
        else:
            _start_hour = dt.hour
        low_period: int = 0
        for i in range(_start_hour, len(self.model.prices)):
            if self.model.prices[i] > self.model.floating_mean:
                break
            if i == dt.hour:
                low_period = 60 - dt.minute
            else:
                low_period += 60
        return low_period

    def _values_are_good(self, i) -> bool:
        checklist = [i, i + 1, i - 23, i - 24]
        non_hours = [dt.hour for dt in self.model.non_hours]
        return all([
            self.model.prices[i] < self.model.floating_mean or self.model.prices[i] < self.model.min_price,
            self.model.prices[i + 1] < self.model.floating_mean or self.model.prices[i + 1] < self.model.min_price,
            not any(item in checklist for item in non_hours)
        ])

    def _calculate_next_start(self, delay_dt=None) -> datetime:
        check_dt = (delay_dt if delay_dt else self.model.now_dt).replace(minute=0, second=0, microsecond=0)
        if self.model.is_cold:
            print("is cold")
        else:
            print("is not cold- will be at:", self.model.cold_limit)
        try:
            if self.model.prices[check_dt.hour] < self.model.floating_mean and self.model.is_cold and not any(
                    [
                        check_dt in self.model.non_hours,
                        (check_dt + timedelta(hours=1)) in self.model.non_hours
                    ]
            ):
                """This hour is cheap enough to start"""
                low_period = self._get_low_period()
                return self._set_start_dt(low_period=low_period)

            loopstart = int((self.model.cold_limit - self.model.now_dt).total_seconds() / 3600) + self.model.now_dt.hour
            i = self.find_lowest_2hr_combination(loopstart, len(self.model.prices) - 1)
            if i:
                return self._set_start_dt_params(i)
        except Exception as e:
            _LOGGER.error(f"Error on getting next start: {e}")
            return datetime.max

    def find_lowest_2hr_combination(self, start_index: int, end_index: int) -> int:
        min_sum = float('inf')
        min_start_index = None
        for i in range(start_index, end_index - 1):
            current_sum = self.model.prices[i] + self.model.prices[i + 1]
            if current_sum < min_sum:
                if self._values_are_good(i):
                    # todo: should also check so that i is not more than 24hr from latest boost.
                    # todo: should also check if we would bump into any demand hours before this hour. So we don't forget that.
                    min_sum = current_sum
                    min_start_index = i
        return min_start_index

    def _set_start_dt_params(self, i: int) -> datetime:
        delay = (i - self.model.now_dt.hour)
        delayed_dt = self.model.now_dt + timedelta(hours=delay)
        low_period = self._get_low_period(override_dt=delayed_dt)
        expected_temp = self.model.current_temp + (delay * self.model.temp_trend)
        new_demand = max(self.model.get_demand_minutes(expected_temp),
                         DEMAND_MINUTES[self.model.preset][Demand.LowDemand])
        return self._set_start_dt(low_period, delayed_dt, True, new_demand)

    def _find_group(self, index: int) -> Group:
        for group in self.model.groups:
            if index in group.hours:
                return group
        return Group(GroupType.UNKNOWN, [])