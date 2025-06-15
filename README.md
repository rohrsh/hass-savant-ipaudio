# Savant IP Audio

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![maintainer](https://img.shields.io/badge/maintainer-%40you-blue.svg)](https://github.com/you)

This is a custom integration for Home Assistant that allows you to control Savant IP Audio devices. It provides media player functionality for your Savant audio zones.

## Legal Disclaimer

This is an **unofficial** integration for Savant IP Audio systems. This integration is not affiliated with, endorsed by, or connected to Savant Systems LLC. Use of this integration is at your own risk. Please review your Savant system's terms of service and ensure you comply with all applicable terms and conditions.

This integration interfaces with the Savant system's web interface in a way that is publicly accessible and does not bypass any security measures. It does not include any Savant proprietary code or reverse-engineered protocols.

I built this for an IP Audio 125 running 9.4.6. I welcome any testers from other Savant systems. 

## Methods

The Savant IP Audio system has an internal web site to monitor and adjust settings. This component pulls JSON every 5 (configurable) seconds. 

Please note that Savant hosts assume they are they master at all times, so changes you make here might not be noticed in your Savant host. Frankly I built this integration so I could ditch the Savant home app. 

## Features

- Control Savant IP Audio zones as media players in Home Assistant
- Adjust volume
- Play/pause control
- Source selection
- Real-time status updates

## Installation

### HACS Installation (Recommended)

1. Make sure you have [HACS](https://hacs.xyz/) installed
2. Add this repository as a custom repository in HACS
3. Search for "Savant IP Audio" in HACS
4. Click Install
5. Restart Home Assistant

### Manual Installation

1. Download the latest release
2. Extract the `savant_ipaudio` folder into your `custom_components` directory
3. Restart Home Assistant

## Configuration

1. In Home Assistant, go to **Configuration** → **Integrations**
2. Click the **+ Add Integration** button
3. Search for "Savant IP Audio"
4. Enter your Savant controller's IP address
5. Follow the configuration flow to complete the setup

## Usage

1. Outputs: Your Savant audio zones will appear as media players in Home Assistant. You might like to rename them. 

2. Inputs: Press configure to rename your inputs. Reload afterwards. 


I tried to get access to the live media streamer metadata (it's Shairport) but I couldn't get this without making changes to the host. 


## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Credits

- [Home Assistant](https://www.home-assistant.io/)
- [HACS](https://hacs.xyz/)

