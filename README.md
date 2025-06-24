# Savant IP Audio

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)


This is a custom integration for Home Assistant that allows you to control Savant IP Audio amps. It provides one Home Assistant media player entity for each Savant audio output zones.

## Legal Disclaimer

This is an **unofficial** integration for Savant IP Audio systems. This integration is not affiliated with, endorsed by, or connected to Savant Systems LLC. Use of this integration is at your own risk. Please review your Savant system's terms of service and ensure you comply with all applicable terms and conditions.

This integration interfaces with the Savant system's web interface in a way that is publicly accessible and does not bypass any security measures. It does not include any Savant proprietary code or reverse-engineered protocols. It does not require any changes to your Savant blueprints. 

<img width="824" alt="image" src="https://github.com/user-attachments/assets/cae643be-327e-4c21-9190-50becf2ed16d" />


## What? 

I built this for a Savant IP Audio 125. It is from a family of powered amps and media streamer. 

https://sav-documentation.s3.amazonaws.com/Product%20Deployment%20Guides/009-1571-04%20Savant%20IP%20Audio%20Deployment%20Guide.pdf

e.g. PAV-SIPA125SM]
5x Inputs:
2 Optical Inputs: one often used for doorbell/PA
2 RCA Inputs 
1 Internal Media Streamer (input 5)

6x Outputs:
4 powered zones
Analogue out
Digital out

I welcome testers with other Savant IP Audio systems. The code may fail with a different number of inputs/outpots. 

Savant Audio Switches are a different beast, see https://github.com/akropp/savantaudio-homeassistant


## Methods

The Savant IP Audio server has an http interface to monitor and adjust settings. This component pulls information every 30 seconds by default.  

Please note that Savant hosts generally assume they are they master of the universe, so changes you make through this interface likely will not be noticed in your Savant host and app. This integration is useful if you want to use your Savant IP Audio in a standalone fashion. 


## Features

- Control Savant IP Audio zones as media players in Home Assistant
- Source selection
- Adjust volume


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

## Setup

1. In Home Assistant, go to **Configuration** → **Integrations**
2. Click the **+ Add Integration** button
3. Search for "Savant IP Audio"
4. Enter your Savant controller's IP address, username/password 


## Configuration

1. Outputs: Your Savant audio zones will appear as numerous media player entities in Home Assistant. You might like to rename them through the UI. 

2. Inputs: Press configure button on the master Savant IP Audio device to rename your inputs. Then reload the device. 


I tried to get access to the live metadata from the media  (it's Shairport) but I couldn't get this without disrupting the flow to the Savant app. 


## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Credits

- [Home Assistant](https://www.home-assistant.io/)
- [HACS](https://hacs.xyz/)
- Vibes from Cursor, ChatGPT and Claude

