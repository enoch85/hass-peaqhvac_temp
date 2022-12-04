import logging
import statistics as stat
import time
from datetime import datetime
import custom_components.peaqhvac.extensionmethods as ex
from custom_components.peaqhvac.service.hub.trend import Gradient
#from custom_components.peaqhvac.service.hvac import peakfinder
from custom_components.peaqhvac.service.hvac.iheater import IHeater
from custom_components.peaqhvac.service.models.demand import Demand
from dataclasses import dataclass

from custom_components.peaqhvac.service.models.hvac_presets import HvacPresets

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = 60
DEFAULT_WATER_BOOST = 600
WAITTIMER_TIMEOUT = 1800


@dataclass
class WaterBoosterModel:
    try_heat_water: bool = False
    heat_water_timer: int = 0
    heat_water_timer_timeout = DEFAULT_WATER_BOOST
    pre_heating: bool = False
    boost: bool = False
    water_is_heating: bool = False


class WaterHeater(IHeater):
    def __init__(self, hvac):
        self._hvac = hvac
        super().__init__(hvac=hvac)
        self._current_temp = None
        self._latest_update = 0
        self._wait_timer = 0
        self._water_temp_trend = Gradient(max_age=3600, max_samples=10, precision=0, ignore=0)
        self.booster_model = WaterBoosterModel()

    @property
    def is_initialized(self) -> bool:
        return self._current_temp is not None

    @property
    def temperature_trend(self) -> float:
        """returns the current temp_trend in C/hour"""
        return self._water_temp_trend.gradient

    @property
    def latest_boost_call(self) -> str:
        """For Lovelace-purposes. Converts and returns epoch-timer to readable datetime-string"""
        if self.booster_model.heat_water_timer > 0:
            return ex.dt_from_epoch(self.booster_model.heat_water_timer)
        return "-"

    @latest_boost_call.setter
    def latest_boost_call(self, val):
        self.booster_model.heat_water_timer = val

    @property
    def current_temperature(self) -> float:
        """The current reported water-temperature in the hvac"""
        return self._current_temp

    @current_temperature.setter
    def current_temperature(self, val):
        try:
            if self._current_temp != float(val):
                self._current_temp = float(val)
                self._water_temp_trend.add_reading(val=float(val), t=time.time())
                self._update_water_heater_operation()
        except ValueError as E:
            _LOGGER.warning(f"unable to set {val} as watertemperature. {E}")
            self.booster_model.try_heat_water = False

    @IHeater.demand.setter
    def demand(self, val):
        self._demand = val

    @property
    def try_heat_water(self) -> bool:
        """Returns true if we should try and heat the water"""
        return self.booster_model.try_heat_water

    @property
    def water_heating(self) -> bool:
        """Return true if the water is currently being heated"""
        return self.temperature_trend > 0 or self.booster_model.pre_heating is True

    def update_demand(self):
        """this function will be the most complex in this class. add more as we go"""
        if time.time() - self._latest_update > UPDATE_INTERVAL:
            self._latest_update = time.time()
            self._demand = self._get_deg_demand()
            if self.control_module:
                self._update_water_heater_operation()

    def _get_deg_demand(self) -> Demand:
        temp = self.current_temperature
        if 0 < temp < 100:
            if temp >= 42:
                return Demand.NoDemand
            if temp > 35:
                return Demand.LowDemand
            if temp >= 25:
                return Demand.MediumDemand
            if temp < 25:
                return Demand.HighDemand
        return Demand.NoDemand

    def _get_water_peak(self, hour: int) -> bool:
        try:
            _prices = []
            _prices.extend(self._hvac.hub.nordpool.prices)
            if self._hvac.hub.nordpool.prices_tomorrow is not None:
                _prices.extend([p for p in self._hvac.hub.nordpool.prices_tomorrow if isinstance(p, (float, int))])
            return all([
                (_prices[hour] == min(_prices) and datetime.now().minute > 20) or (_prices[hour+1] == min(_prices) and _prices[hour+1]/_prices[hour] >= 0.7 and datetime.now().minute >= 30),
                _prices[hour] < stat.mean(_prices)/2
                        ])
        except:
            _LOGGER.debug("Could not calc peak water hours")
            return False

    def _update_water_heater_operation(self):
        if self.is_initialized:
            if self._hvac.hub.sensors.set_temp_indoors.preset == HvacPresets.Normal:
                self._set_water_heater_operation_home()
            elif self._hvac.hub.sensors.set_temp_indoors.preset == HvacPresets.Away:
                self._set_water_heater_operation_away()

    def _set_water_heater_operation_home(self):
        if self._hvac.hub.sensors.peaqev_installed:
            if float(self._hvac.hub.sensors.peaqev_facade.exact_threshold) >= 100:
                return
        if self._get_water_peak(datetime.now().hour):
            _LOGGER.debug("Current hour is identified as a good hour to boost water")
            self.booster_model.boost = True
            self._toggle_boost(timer_timeout=3600)
        try:
            offsets = self._hvac.hub.offset.getoffset(
            prices=self._hvac.hub.nordpool.prices,
            prices_tomorrow=self._hvac.hub.nordpool.prices_tomorrow
            )
            current_offset = offsets[0][datetime.now().hour]

            if current_offset <= 0 and datetime.now().minute > 10:
                if 0 < self.current_temperature <= 30:
                    self.booster_model.pre_heating = True
                    self._toggle_boost(timer_timeout=None)
            elif current_offset > 0 and datetime.now().minute > 10:
                if 0 < self.current_temperature <= 42:
                    self.booster_model.pre_heating = True
                    self._toggle_boost(timer_timeout=None)
        except Exception as e:
            _LOGGER.debug(f"Can't read offsets for water-heating: {e}")

    def _set_water_heater_operation_away(self):
        if self._hvac.hub.sensors.peaqev_installed:
            if float(self._hvac.hub.sensors.peaqev_facade.exact_threshold) >= 100:
                return
        try:
            if datetime.now().minute > 10:
                if 0 < self.current_temperature <= 30:
                    self.booster_model.pre_heating = True
                    self._toggle_boost(timer_timeout=None)
        except Exception as e:
            _LOGGER.debug(f"Could not properly update water operation in away-mode: {e}")

    def _toggle_boost(self, timer_timeout: int = None) -> None:
        if self.booster_model.try_heat_water:
            if self.booster_model.heat_water_timer_timeout > 0:
                if time.time() - self.booster_model.heat_water_timer > self.booster_model.heat_water_timer_timeout:
                    self.booster_model.try_heat_water = False
                    self._wait_timer = time.time()
        elif (self.booster_model.pre_heating or self.booster_model.boost) and time.time() - self._wait_timer > WAITTIMER_TIMEOUT:
            self.booster_model.try_heat_water = True
            self.booster_model.heat_water_timer = time.time()
            self.booster_model.heat_water_timer_timeout = timer_timeout if timer_timeout is not None else DEFAULT_WATER_BOOST
