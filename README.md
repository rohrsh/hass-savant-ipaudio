# Savant IP Audio

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![maintainer](https://img.shields.io/badge/maintainer-%40rohrsh-blue.svg)](https://github.com/rohrsh)

This is a custom integration for Home Assistant that allows you to control Savant IP Audio devices. It provides media player functionality for your Savant audio zones.

## Legal Disclaimer

This is an **unofficial** integration for Savant IP Audio systems. This integration is not affiliated with, endorsed by, or connected to Savant Systems LLC. Use of this integration is at your own risk. Please review your Savant system's terms of service and ensure you comply with all applicable terms and conditions.

This integration interfaces with the Savant system's web interface in a way that is publicly accessible and does not bypass any security measures. It does not include any Savant proprietary code or reverse-engineered protocols.

I built this for an IP Audio 125 running 9.4.6. I welcome any testers from other Savant systems — if something doesn't look right, please attach the integration's diagnostics download (Settings → Devices & services → Savant IP Audio → ⋮ → Download diagnostics) to your issue. Credentials, host and device ID are redacted.

## Methods

The Savant IP Audio system has an internal web site to monitor and adjust settings. This integration polls its JSON every 30 seconds (configurable), and every 3 seconds for half a minute after you change something from Home Assistant.

Please note that Savant hosts assume they are the master at all times, so changes you make here might not be noticed in your Savant host. Frankly I built this integration so I could ditch the Savant home app.

## Features

- Each Savant output (zone) is a media player in Home Assistant
- Turn zones on and off, adjust volume (3 dB steps) and mute
- Source selection, with your own names for the inputs
- Turning a zone on returns to its last used source, or to a source you pick per zone. A zone that is already on is left alone, so `media_player.turn_on` is safe to call from automations
- Zones become unavailable when the device stops responding (after three missed polls) and recover on their own
- DSP settings reported by the device are exposed as state attributes
- Reconfigure (new IP address) and re-authentication without removing the integration

The zones are outputs of a matrix switch, not independent players: zones listening to the same input hear the same thing. There is no play/pause or track metadata.

## Requirements

Home Assistant 2025.8 or newer.

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

1. In Home Assistant, go to **Settings** → **Devices & services**
2. Click the **+ Add integration** button
3. Search for "Savant IP Audio"
4. Enter your Savant device's IP address, and the username and password of its web interface (factory default `RPM` / `RPM`)

## Usage

1. Outputs: Your Savant audio zones appear as media players, named as they are on the device. You might like to rename them and assign them to areas.

2. Options: Press **Configure** on the integration to
   - name your inputs (clear a name to go back to the device's name),
   - choose the source each zone turns on with (default: last used source),
   - change the polling interval.

   Changes apply immediately.

I tried to get access to the live media streamer metadata (it's Shairport) but I couldn't get this without making changes to the host.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Credits

- [Home Assistant](https://www.home-assistant.io/)
- [HACS](https://hacs.xyz/)
