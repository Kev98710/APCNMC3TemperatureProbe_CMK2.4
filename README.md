# APCNMC3TemperatureProbe_CMK2.4  
Script was rebuilt from TwoByte.blog to work with my APC Li-Ion (Smart-UPS 1500) USV's with APC NMC3 AP9641 on Checkmk 2.4.0p14.  
I also added an auto conversion for Fahrenheit to Celsius.  
I was only able to test with an European APC NMC3 Networking Card and CheckMK 2.4.0p21 set to Celsius.  
If someone could confirm or deny if this is working on Fahrenheit Setups that would be awesome.  
  
##### Installation  
Change user to your CMK site  
su - <site name>  
  
Due to Changes in cmk_addons structure, you need to create the following folders:  
  
~/local/lib/python3/cmk_addons/plugins/apc_extsensors  
~/local/lib/python3/cmk_addons/plugins/apc_extsensors/agent_based  
  
Create the file  
  
~/local/lib/python3/cmk_addons/plugins/apc_extsensors/agent_based/apc_temperature_sensor.py  
  
Then copy the contents of the project file into it.  
  
Afterwards, reload the Checkmk configuration:  
  
cmk -R  
  
#### Testing  
  
Force a service rediscovery on the host:  
cmk -II hostname  # force Re-Discovery
  
To verify that the UPS output is being processed correctly:  
cmk -v hostname    # Check Services
  
##### Additional Information  
This script was rebuilt with the assistance of Claude AI.  
