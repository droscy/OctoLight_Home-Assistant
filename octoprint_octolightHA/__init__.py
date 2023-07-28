# -*- coding: utf-8 -*-

import re
import time
import flask
import requests
import octoprint.plugin

from octoprint.events import Events
from requests.exceptions import InvalidURL, ConnectionError


CONFIG_ADDRESS = 'address'
CONFIG_API_KEY = 'api_key'
CONFIG_ENTITY_ID = 'entity_id'
CONFIG_VERIFY_CERTIFICATE = 'verify_certificate'


def _parse_status(status):
    if status == 'on':
        return True
    else:
        return False


class OctoLightHAPlugin(
        octoprint.plugin.AssetPlugin,
        octoprint.plugin.StartupPlugin,
        octoprint.plugin.TemplatePlugin,
        octoprint.plugin.SimpleApiPlugin,
        octoprint.plugin.SettingsPlugin,
        octoprint.plugin.EventHandlerPlugin,
        octoprint.plugin.RestartNeedingPlugin,
    ):

    def __init__(self):
        self.config = dict()
        self.isLightOn = False  # binded to js static file

    def get_settings_defaults(self):
        return {
            CONFIG_ADDRESS: '',
            CONFIG_API_KEY: '',
            CONFIG_ENTITY_ID: '',
            CONFIG_VERIFY_CERTIFICATE: False,
        }

    def on_settings_initialized(self):
        self.reload_settings()

    def reload_settings(self):
        for k, v in self.get_settings_defaults().items():
            if type(v) == str:
                v = self._settings.get([k])
            elif type(v) == bool:
                v = self._settings.get_boolean([k])
            elif type(v) == int:
                v = self._settings.get_int([k])
            elif type(v) == float:
                v = self._settings.get_float([k])

            self.config[k] = v
            self._logger.debug(f'{k}: {v}')

    def get_template_configs(self):
        return [
            dict(type='navbar', custom_bindings=True),
            dict(type='settings', custom_bindings=True),
        ]

    def get_assets(self):
        return dict(
            js=['js/octolightHA.js'],
            css=['css/octolightHA.css'],
        )

    def is_config_completed(self):
        try:
            return bool(self.config[CONFIG_ADDRESS]
                    and self.config[CONFIG_ENTITY_ID]
                    and self.config[CONFIG_API_KEY])

        except Exception:
            return False

    def is_HA_state_on(self):
        self._logger.debug('getting current status of light')

        if not self.is_config_completed():
            self._logger.warning('configuration is not complete, light controls are disabled')
            return False

        url = f'{self.config[CONFIG_ADDRESS]}/api/states/{self.config[CONFIG_ENTITY_ID]}'
        headers = dict(Authorization=f'Bearer {self.config[CONFIG_API_KEY]}')
        verify = self.config[CONFIG_VERIFY_CERTIFICATE]

        status = None
        result = None

        try:
            response = requests.get(url, headers=headers, verify=verify)

        except (InvalidURL, ConnectionError) as err:
            self._logger.error(f'unable to communicate with server, please check settings, error: {err}')

        except Exception as err:
            self._logger.exception(f'exception while making API call: {err}')

        else:
            try:
                status = response.json()['state']

            except Exception as err:
                self._logger.exception(f'exception while parsing API result: {err}')

            else:
                result = _parse_status(status)

        self._logger.debug(f'the light is currently {status}')
        return result

    def toggle_HA_state(self):
        self._logger.debug('toggling light')
        old_isLightOn = self.isLightOn

        if not self.is_config_completed():
            self._logger.warning('configuration is not complete, light controls are disabled')
            return

        _entity_id = self.config[CONFIG_ENTITY_ID]
        _entity_domain = _entity_id.split('.')[0]

        url = f'{self.config[CONFIG_ADDRESS]}/api/services/{_entity_domain}/toggle'
        headers = dict(Authorization=f'Bearer {self.config[CONFIG_API_KEY]}')
        data = {'entity_id': _entity_id}
        verify = self.config[CONFIG_VERIFY_CERTIFICATE]

        result = None

        try:
            response = requests.post(url, headers=headers, json=data, verify=verify)

        except (InvalidURL, ConnectionError) as err:
            self._logger.error(f'unable to communicate with server, please check settings, error: {err}')

        except Exception as err:
            self._logger.exception(f'exception while making API call: {err}')

        else:
            try:
                status = response.json()[0]['state']

            except (IndexError, KeyError) as err:
                self._logger.debug('new status not reported, sleeping...')
                time.sleep(1)
                result = self.is_HA_state_on()

            except Exception as err:
                self._logger.exception(f'exception while parsing API result: {err}')

            else:
                result = _parse_status(status)

        if result != old_isLightOn:
            self._logger.info('light switched {}'.format('ON' if result else 'OFF'))

        else:
            self._logger.warning('cannot toggle light')

        return result

    def toggle_light(self):
        self.isLightOn = self.toggle_HA_state()
        self._plugin_manager.send_plugin_message(self._identifier, dict(isLightOn=self.isLightOn))

    def refresh_light_status(self):
        self.isLightOn = self.is_HA_state_on()
        self._plugin_manager.send_plugin_message(self._identifier, dict(isLightOn=self.isLightOn))

    def on_after_startup(self):
        self._logger.info('OctoLightHA started, listening for GET requests')
        self.refresh_light_status()

    def on_api_get(self, request):
        self._logger.debug(f'API REQUEST isLightOn: {self.isLightOn}')
        action = request.args.get('action', default='toggle', type=str)
        self._logger.debug(f'running API action {action}')

        old_isLightOn = self.isLightOn
        if action == 'toggle':
            self.toggle_light()

            if old_isLightOn != self.isLightOn:
                self._logger.debug('TOGGLE: light state changed')

            return flask.jsonify(state=self.isLightOn)

        elif action == 'getState':
            self.refresh_light_status()

            if old_isLightOn != self.isLightOn:
                self._logger.debug('GETSTATE: light state changed.')

            return flask.jsonify(state=self.isLightOn)

        elif action == 'turnOn':
            if not self.isLightOn:
                self.toggle_light()

            if old_isLightOn != self.isLightOn:
                self._logger.debug('TURNON: light state changed.')

            return flask.jsonify(state=self.isLightOn)

        elif action == 'turnOff':
            if self.isLightOn:
                self.toggle_light()

            if old_isLightOn != self.isLightOn:
                self._logger.debug('TURNOFF: light state changed.')

            return flask.jsonify(state=self.isLightOn)

        else:
            return flask.jsonify(error='action not recognized')

    def on_event(self, event, payload):
        if event == Events.CLIENT_OPENED:
            self.refresh_light_status()

    def on_settings_save(self, data):
        if CONFIG_ADDRESS in data:
            data[CONFIG_ADDRESS] = re.sub(r'/*$', '', data[CONFIG_ADDRESS])

        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self.reload_settings()

    def get_update_information(self):
        return dict(
            octolightHA=dict(
                displayName='OctoLightHA',
                displayVersion=self._plugin_version,

                type='github_release',
                current=self._plugin_version,

                user='droscy',
                repo='OctoLightHA',
                pip='https://github.com/droscy/OctoLight_Home-Assistant/archive/{target}.zip'
            )
        )

    def register_custom_events(self):
        return ['light_state_changed']

__plugin_pythoncompat__ = '>=3.0,<4'
__plugin_implementation__ = OctoLightHAPlugin()

__plugin_hooks__ = {
    'octoprint.plugin.softwareupdate.check_config':
    __plugin_implementation__.get_update_information
}

# vim: ts=4 sw=4 et
