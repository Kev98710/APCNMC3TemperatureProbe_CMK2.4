# Location: ~/local/lib/python3/cmk_addons/plugins/apc_extsensors/agent_based/apc_temperature_sensor.py
from cmk.agent_based.v2 import (
    all_of,
    contains,
    exists,
    get_value_store,
    CheckPlugin,
    Service,
    SNMPSection,
    SNMPTree,
    State,
)
from cmk.plugins.lib.temperature import check_temperature

# Mapping of APC sensor alarm status codes to CheckMK states
# 1 = disconnected/unknown, 2 = normal, 3 = warning, 4 = critical
APC_SENSOR_STATUS = {
    1: State.UNKNOWN,
    2: State.OK,
    3: State.WARN,
    4: State.CRIT,
}

def fahrenheit_to_celsius(temp_f):
    """
    Convert temperature from Fahrenheit to Celsius.

    Args:
        temp_f: Temperature in Fahrenheit (float or None)

    Returns:
        Temperature in Celsius or None if input is None
    """
    if temp_f is None:
        return None
    return (temp_f - 32) * 5.0 / 9.0

def celsius_to_fahrenheit(temp_c):
    """
    Convert temperature from Celsius to Fahrenheit.

    Args:
        temp_c: Temperature in Celsius (float or None)

    Returns:
        Temperature in Fahrenheit or None if input is None
    """
    if temp_c is None:
        return None
    return (temp_c * 9.0 / 5.0) + 32

def get_target_unit_from_params(params):
    """
    Determine the target temperature unit from CheckMK parameters.

    CheckMK's temperature library uses Celsius internally by default.
    Users can configure their preferred unit in the temperature ruleset.

    Args:
        params: Check parameters from 'temperature' ruleset

    Returns:
        'celsius' or 'fahrenheit' based on params, defaults to 'celsius'
    """
    # Check if user has explicitly set input_unit in temperature ruleset
    # If not specified, CheckMK uses Celsius as default
    if isinstance(params, dict):
        input_unit = params.get('input_unit', 'celsius')
        # Normalize to lowercase for comparison
        if isinstance(input_unit, str):
            return input_unit.lower()

    # Default to Celsius (CheckMK's internal default)
    return 'celsius'

def detect_threshold_unit(thresholds):
    """
    Detect if threshold values are in Celsius or Fahrenheit.

    Strategy: Compare threshold values to make an educated guess
    - Typical Celsius thresholds for data centers: 20-35°C
    - Same values in Fahrenheit: 68-95°F
    - If thresholds are > 50, likely Fahrenheit
    - If thresholds are < 50, likely Celsius

    This is a heuristic approach since APC MIB documentation indicates
    the unit depends on NMC localization settings (US=Fahrenheit, EU=Celsius).

    Args:
        thresholds: List of threshold values from multiple sensors

    Returns:
        'celsius' or 'fahrenheit' (best guess)
    """
    valid_thresholds = [t for t in thresholds if t is not None and t > 0]

    if not valid_thresholds:
        # No valid thresholds, assume Celsius (most common)
        return 'celsius'

    # Calculate average of thresholds
    avg_threshold = sum(valid_thresholds) / len(valid_thresholds)

    # Heuristic: If average threshold > 50, likely Fahrenheit
    # Reasoning: Data center temps rarely exceed 40°C (104°F)
    # but commonly have thresholds around 70-80°F (21-27°C)
    if avg_threshold > 50:
        return 'fahrenheit'
    else:
        return 'celsius'

def normalize_temperature_data(temp_current_f, temp_warn, temp_crit, threshold_unit, target_unit):
    """
    Normalize all temperature values to the target unit.

    This function handles the conversion of potentially mixed-unit temperature data:
    - Current temperature from device is always in Fahrenheit (OID .5)
    - Threshold temperatures depend on NMC localization (detected automatically)
    - Target unit depends on CheckMK global/ruleset configuration

    Args:
        temp_current_f: Current temperature in Fahrenheit (float)
        temp_warn: Warning threshold (float or None, unit depends on threshold_unit)
        temp_crit: Critical threshold (float or None, unit depends on threshold_unit)
        threshold_unit: Unit of threshold values ('celsius' or 'fahrenheit')
        target_unit: Target unit ('celsius' or 'fahrenheit')

    Returns:
        Tuple of (current_temp, warn_threshold, crit_threshold) in target unit
    """
    # Step 1: Convert current temperature to target unit
    if target_unit == 'fahrenheit':
        current = temp_current_f  # Already in Fahrenheit
    else:
        current = fahrenheit_to_celsius(temp_current_f)  # Convert to Celsius

    # Step 2: Convert thresholds to target unit
    if threshold_unit == target_unit:
        # Thresholds already in target unit, no conversion needed
        warn = temp_warn
        crit = temp_crit
    elif threshold_unit == 'celsius' and target_unit == 'fahrenheit':
        # Convert thresholds from Celsius to Fahrenheit
        warn = celsius_to_fahrenheit(temp_warn) if temp_warn is not None else None
        crit = celsius_to_fahrenheit(temp_crit) if temp_crit is not None else None
    elif threshold_unit == 'fahrenheit' and target_unit == 'celsius':
        # Convert thresholds from Fahrenheit to Celsius
        warn = fahrenheit_to_celsius(temp_warn) if temp_warn is not None else None
        crit = fahrenheit_to_celsius(temp_crit) if temp_crit is not None else None
    else:
        # Should not happen, but handle gracefully
        warn = temp_warn
        crit = temp_crit

    return current, warn, crit

def parse_apc_rackpdu_sensor_temp_v2(string_table):
    """
    Parse SNMP data from APC Rack PDU temperature sensors.

    This function stores raw values and detects the unit of threshold values:
    - Current temperature is always in Fahrenheit (OID .5 - TempF)
    - Thresholds unit depends on NMC3 localization settings:
      * US localization → Fahrenheit
      * EU localization → Celsius
    - Unit detection happens via heuristic analysis of threshold values

    Args:
        string_table: List containing two SNMP tables:
                     [0] = status_table with current readings
                     [1] = config_table with threshold settings

    Returns:
        Dictionary with sensor_name as key and sensor data as value,
        plus special key '_threshold_unit' indicating detected unit
    """
    parsed = {}
    status_table, config_table = string_table

    # First pass: Build a dictionary of threshold configurations for each sensor
    # Store thresholds in their original unit (unknown at this point)
    config_dict = {}
    all_thresholds = []  # Collect all thresholds for unit detection

    for sensor_name, port_name, temp_high, temp_max in config_table:
        try:
            # Store thresholds as-is (unit will be detected later)
            temp_high_val = float(temp_high) if temp_high else None
            temp_max_val = float(temp_max) if temp_max else None

            config_dict[sensor_name] = {
                'temp_high': temp_high_val,
                'temp_max': temp_max_val,
            }

            # Collect for unit detection
            if temp_high_val is not None:
                all_thresholds.append(temp_high_val)
            if temp_max_val is not None:
                all_thresholds.append(temp_max_val)

        except (ValueError, TypeError):
            # If conversion fails, store None values for this sensor
            config_dict[sensor_name] = {
                'temp_high': None,
                'temp_max': None,
            }

    # Detect the unit of threshold values based on their magnitude
    # This is necessary because APC NMC3 cards return thresholds in different units
    # depending on localization settings (US=°F, EU=°C)
    threshold_unit = detect_threshold_unit(all_thresholds)

    # Store the detected threshold unit in parsed data
    # This will be used by the check function for correct conversion
    parsed['_threshold_unit'] = threshold_unit

    # Second pass: Process current sensor readings and combine with thresholds
    for sensor_name, port_name, temp_f_raw, status_code in status_table:
        # Current temperature is always in Fahrenheit (OID .5)
        # This is consistent regardless of NMC localization
        try:
            temp_f = float(temp_f_raw)  # Temperature in Fahrenheit
        except (ValueError, TypeError):
            # Skip this sensor if temperature value is invalid
            continue

        # Retrieve threshold values for this sensor
        thresholds = config_dict.get(sensor_name, {})
        temp_high = thresholds.get('temp_high')
        temp_max = thresholds.get('temp_max')

        # Store parsed data in original units (no conversion yet)
        # Unit conversion will happen in check function based on:
        # 1. Detected threshold unit (from NMC localization)
        # 2. Target unit (from CheckMK configuration)
        parsed[sensor_name] = {
            'temperature_f': temp_f,                                    # Current temp in Fahrenheit (always)
            'status_code': int(status_code) if status_code else 1,      # Device alarm status (1-4)
            'warn': temp_high,                                          # Warning threshold (unit in _threshold_unit)
            'crit': temp_max,                                           # Critical threshold (unit in _threshold_unit)
        }

    return parsed

# SNMP section definition for APC Rack PDU temperature sensors
snmp_section_apc_rackpdu_sensor_temp_v2 = SNMPSection(
    # Internal name used by CheckMK to identify this section
    name='apc_rackpdu_sensor_temp_v2',

    # Detection rule: Match devices that identify as "APC Web/SNMP"
    # and have the temperature sensor status OID tree
    detect=all_of(
        contains('.1.3.6.1.2.1.1.1.0', 'APC Web/SNMP'),          # sysDescr contains APC identifier
        exists('.1.3.6.1.4.1.318.1.1.25.1.2.1.3.*'),             # Sensor status table exists
    ),

    # Function to parse the fetched SNMP data
    parse_function=parse_apc_rackpdu_sensor_temp_v2,

    # Fetch two SNMP tables: current status and configuration thresholds
    fetch=[
        # Table 1: Current sensor status and readings
        SNMPTree(
            base='.1.3.6.1.4.1.318.1.1.25.1.2.1',                # rPDU2SensorTempHumidityStatusTable
            oids=[
                '3',   # rPDU2SensorTempHumidityStatusName - unique sensor identifier
                '4',   # rPDU2SensorTempHumidityStatusCommStatus - communication status with sensor
                '5',   # rPDU2SensorTempHumidityStatusTempF - current temperature (always Fahrenheit)
                '10',  # rPDU2SensorTempHumidityStatusAlarmStatus - alarm state (1=unknown, 2=ok, 3=warn, 4=crit)
            ],
        ),
        # Table 2: Sensor configuration including alarm thresholds
        SNMPTree(
            base='.1.3.6.1.4.1.318.1.1.25.1.4.1',                # rPDU2SensorTempHumidityConfigTable
            oids=[
                '3',   # rPDU2SensorTempHumidityConfigName - sensor name (matches status table)
                '4',   # rPDU2SensorTempHumidityConfigLocation - physical location/description
                '7',   # rPDU2SensorTempHumidityConfigTempHighThresh - warning threshold (°C or °F depending on localization)
                '8',   # rPDU2SensorTempHumidityConfigTempMaxThresh - critical threshold (°C or °F depending on localization)
            ],
        ),
    ],
)

def discovery_apc_rackpdu_sensor_temp_v2(section):
    """
    Discover available temperature sensors.

    Args:
        section: Parsed sensor data dictionary from parse function

    Yields:
        Service objects for each discovered sensor
    """
    # Create a service for each sensor found in the parsed data
    # Skip the special '_threshold_unit' entry
    for sensor in section:
        if not sensor.startswith('_'):  # Skip metadata entries
            yield Service(item=sensor)

def check_apc_rackpdu_sensor_temp_v2(item, params, section):
    """
    Check function for temperature monitoring with automatic unit detection and conversion.

    This function handles three different temperature unit scenarios:
    1. Current temperature (always Fahrenheit from device)
    2. Threshold values (Celsius or Fahrenheit depending on NMC localization)
    3. Target unit (Celsius or Fahrenheit from CheckMK configuration)

    The function automatically detects the threshold unit and converts all values
    to match CheckMK's configured unit.

    Args:
        item: Sensor name (service identifier)
        params: User-configured check parameters from 'temperature' ruleset
        section: Parsed sensor data from parse function

    Yields:
        Result objects from check_temperature function
    """
    # Verify that the requested sensor exists in parsed data
    if item not in section or item.startswith('_'):
        return  # Sensor not found or is metadata, return nothing

    # Extract sensor data from parsed section
    data = section[item]
    temperature_f = data['temperature_f']  # Current temperature in Fahrenheit (always from device)
    status_code = data['status_code']      # Device-reported alarm status (1-4)
    warn = data['warn']                    # Warning threshold (unit detected in parse)
    crit = data['crit']                    # Critical threshold (unit detected in parse)

    # Get detected threshold unit from parse function
    # This tells us if the NMC is configured for US (°F) or EU (°C) localization
    threshold_unit = section.get('_threshold_unit', 'celsius')

    # Determine target temperature unit from CheckMK configuration
    # This respects global settings and user-configured temperature rulesets
    target_unit = get_target_unit_from_params(params)

    # Convert all temperature values to the target unit
    # This handles three-way conversion:
    # 1. Current temp: Fahrenheit → target unit
    # 2. Thresholds: detected unit → target unit
    temperature, warn_converted, crit_converted = normalize_temperature_data(
        temp_current_f=temperature_f,
        temp_warn=warn,
        temp_crit=crit,
        threshold_unit=threshold_unit,
        target_unit=target_unit
    )

    # Prepare device-level thresholds for check_temperature function
    # Only pass thresholds if both warning and critical values are available
    # If either is None, CheckMK will use only user-configured thresholds
    dev_levels = None
    if warn_converted is not None and crit_converted is not None:
        dev_levels = (warn_converted, crit_converted)  # Tuple in target unit

    # Perform temperature check using CheckMK's standard temperature library
    # All values are now in the same unit (either Celsius or Fahrenheit)
    # matching CheckMK's global/ruleset configuration
    yield from check_temperature(
        reading=temperature,           # Current temperature value in target unit
        params=params,                 # User-defined parameters from 'temperature' ruleset
        unique_name='check_apc_rackpdu_sensor_temp_v2.%s' % item,  # Unique identifier for RRD database
        value_store=get_value_store(), # Persistent storage for trend calculation and rate checks
        dev_levels=dev_levels,         # Device-reported thresholds in target unit or None
        dev_status=APC_SENSOR_STATUS.get(status_code, State.UNKNOWN),  # Device alarm state mapped to CheckMK state
        dev_status_name=item,          # Label for device status in check output
    )

# Register the check plugin with CheckMK
check_plugin_apc_rackpdu_sensor_temp_v2 = CheckPlugin(
    # Internal plugin name (must match SNMP section name)
    name='apc_rackpdu_sensor_temp_v2',

    # Service name template shown in CheckMK UI
    # %s will be replaced with the sensor name (item)
    service_name='%s Temperature',

    # Function to discover services (sensors)
    discovery_function=discovery_apc_rackpdu_sensor_temp_v2,

    # Function to perform the actual check
    check_function=check_apc_rackpdu_sensor_temp_v2,

    # Default parameters if user hasn't configured any
    # Empty dict means use CheckMK's global temperature defaults
    check_default_parameters={},

    # Links this check to CheckMK's 'temperature' ruleset
    # Users can configure temperature thresholds and units via WATO
    check_ruleset_name='temperature',
)
