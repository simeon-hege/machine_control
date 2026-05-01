# -*- coding: utf-8 -*-

import json
import logging
import threading
import time
import psycopg2
from psycopg2 import errors as pg_errors

from odoo import api, fields, models, _, SUPERUSER_ID
from odoo.exceptions import UserError

from .focas_native import FocasClient, FocasError


_logger = logging.getLogger(__name__)


class MachineControlCncDevice(models.Model):
	_name = 'machine_control.cnc.device'
	_description = 'FANUC CNC Device'

	name = fields.Char(required=True)
	host = fields.Char(required=True, string="Machine IP", help='FANUC controller IP or hostname')
	port = fields.Integer(default=8193, required=True)
	timeout = fields.Integer(default=10, required=True, help='Connection timeout in seconds')
	active = fields.Boolean(default=True)

	macro_no = fields.Integer(string='Macro Number', default=500)
	macro_value = fields.Float(string='Macro Value', digits=(16, 6))
	macro_decimals = fields.Integer(string='Macro Decimals', default=4)

	last_read_at = fields.Datetime(readonly=True)
	last_status = fields.Selection([
		('ok', 'OK'),
		('error', 'Error'),
	], readonly=True)
	last_error = fields.Text(readonly=True)
	last_position_data = fields.Text(readonly=True)

	def _serialize_payload(self, payload):
		return json.dumps(payload, indent=2, sort_keys=True, default=str)

	def action_read_position(self):
		self.ensure_one()
		
		try:
			with FocasClient(self.host, self.port, self.timeout) as focas:
				sysinfo = focas.read_sysinfo()
				position = focas.read_position()
				
				payload = {
					'device': self.name,
					'host': self.host,
					'port': self.port,
					'sysinfo': sysinfo,
					'position': {
						'method': 'cnc_rdposition',
						'data': position,
					},
				}

			serialized = self._serialize_payload(payload)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'ok',
				'last_error': False,
				'last_position_data': serialized,
			})
		except (FocasError, OSError) as exc:
			message = str(exc)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'error',
				'last_error': message,
			})
			raise UserError(_('Failed to read FANUC position data: %s') % message) from exc
		except Exception as exc:
			_logger.exception('Unexpected FANUC read error')
			raise UserError(_('Unexpected FANUC read error: %s') % str(exc)) from exc

		return True

	def read_position_no_snapshot(self):
		"""Read position from the machine and update last_* fields, but do NOT
		create a persistent snapshot. Returns a dict describing the result.
		This is intended for frequent live polling (every second) while
		avoiding snapshot creation on every poll.
		"""
		self.ensure_one()
		try:
			with FocasClient(self.host, self.port, self.timeout) as focas:
				sysinfo = focas.read_sysinfo()
				position = focas.read_position()
				payload = {
					'device': self.name,
					'host': self.host,
					'port': self.port,
					'sysinfo': sysinfo,
					'position': {
						'method': 'cnc_rdposition',
						'data': position,
					},
				}

			serialized = self._serialize_payload(payload)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'ok',
				'last_error': False,
				'last_position_data': serialized,
			})
			return {'status': 'ok', 'payload': payload}
		except (FocasError, OSError) as exc:
			message = str(exc)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'error',
				'last_error': message,
			})
			return {'status': 'error', 'error': message}
		except Exception as exc:
			_logger.exception('Unexpected FANUC read error (no snapshot)')
			message = str(exc)
			return {'status': 'error', 'error': message}

	def sample_position(self):
		"""Read position from the machine but do NOT write any fields.
		Return a dict with status and payload. Intended for high-frequency
		sampling where we don't want to update the device row on every read.
		"""
		self.ensure_one()
		try:
			with FocasClient(self.host, self.port, self.timeout) as focas:
				sysinfo = focas.read_sysinfo()
				position = focas.read_position()
				payload = {
					'device': self.name,
					'host': self.host,
					'port': self.port,
					'sysinfo': sysinfo,
					'position': {
						'method': 'cnc_rdposition',
						'data': position,
					},
				}
				return {'status': 'ok', 'payload': payload}
		except (FocasError, OSError) as exc:
			return {'status': 'error', 'error': str(exc)}
		except Exception as exc:
			_logger.exception('Unexpected FANUC sample error')
			return {'status': 'error', 'error': str(exc)}

	def action_write_macro(self):
		self.ensure_one()
		if self.macro_no <= 0:
			raise UserError(_('Macro number must be greater than 0.'))

		try:
			with FocasClient(self.host, self.port, self.timeout) as focas:
				focas.write_macro(self.macro_no, self.macro_value, self.macro_decimals)
				read_back = focas.read_macro(self.macro_no)

			payload = {
				'device': self.name,
				'host': self.host,
				'port': self.port,
				'macro_write': {
					'macro_no': self.macro_no,
					'value': self.macro_value,
					'decimals': self.macro_decimals,
				},
				'macro_read_back': read_back,
			}

			serialized = self._serialize_payload(payload)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'ok',
				'last_error': False,
				'last_position_data': serialized,
			})
		except (FocasError, OSError) as exc:
			message = str(exc)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'error',
				'last_error': message,
			})
			raise UserError(_('Failed to write FANUC macro: %s') % message) from exc

		return True

	def action_open_live(self):
		"""Open the backend OWL live view for this device."""
		self.ensure_one()
		return {
			'type': 'ir.actions.client',
			'tag': 'machine_control.live_view',
			'name': _('Live: %s') % self.name,
			'params': {
				'device_id': self.id,
				'device_name': self.name,
			},
			'target': 'current',
		}

	def _get_latest_live_payload(self):
		self.ensure_one()
		live = self.env['machine_control.cnc.live'].sudo().search([
			('device_id', '=', self.id),
		], order='ts desc', limit=1)
		if live:
			try:
				payload = json.loads(live.payload) if live.payload else None
			except Exception:
				payload = None
			return {
				'status': self.last_status or 'ok',
				'last_read_at': str(live.ts) if live.ts else None,
				'payload': payload,
			}

		try:
			payload = json.loads(self.last_position_data) if self.last_position_data else None
		except Exception:
			payload = None
		return {
			'status': self.last_status or 'ok',
			'last_read_at': str(self.last_read_at) if self.last_read_at else None,
			'payload': payload,
		}

	def get_live_data(self, refresh=False):
		"""RPC method used by the OWL live view."""
		self.ensure_one()
		device = self.sudo()
		if refresh:
			result = device.read_position_no_snapshot()
			if result.get('status') == 'ok':
				return {
					'status': 'ok',
					'last_read_at': str(device.last_read_at) if device.last_read_at else None,
					'payload': result.get('payload'),
				}
			return {
				'status': 'error',
				'last_read_at': str(device.last_read_at) if device.last_read_at else None,
				'error': result.get('error'),
				'payload': None,
			}
		return device._get_latest_live_payload()

	def get_live_update(self, last_seen=None, timeout=30.0, poll_interval=1.0):
		"""Long-poll RPC endpoint for the OWL live view."""
		self.ensure_one()
		device = self.sudo()
		start = time.time()
		timeout = max(float(timeout or 0.0), 1.0)
		poll_interval = max(float(poll_interval or 0.0), 0.1)

		current = device._get_latest_live_payload()
		if current.get('last_read_at') != last_seen:
			return current

		while time.time() - start < timeout:
			time.sleep(poll_interval)
			current = device._get_latest_live_payload()
			if current.get('last_read_at') != last_seen:
				return current

		return current

	def jog_from_live(self, axis, value):
		"""RPC helper used by the OWL live view jog controls."""
		self.ensure_one()
		if axis is None or value is None:
			return {'result': 'error', 'error': 'axis and value required'}
		try:
			res = self.sudo()._jog_axis(axis, value)
			if isinstance(res, dict) and 'candidates' in res:
				return {'result': 'ok', 'candidates': res['candidates']}
			return {'result': 'ok', 'read_back': res}
		except Exception as exc:
			_logger.exception('jog failed for device=%s axis=%s', self.id, axis)
			return {'result': 'error', 'error': str(exc)}

	def _jog_axis(self, axis, value):
		"""Send a jog command for the given axis.
		This implementation writes to a macro number on the controller.
		Mapping: macro_no + axis_index (x=0,y=1,z=2,a=3).
		The machine must have macros configured to perform the physical jog
		when the macro value changes.
		"""
		self.ensure_one()
		names = ['x', 'y', 'z', 'a']
		axis = (axis or '').lower()
		if axis not in names:
			raise UserError(_('Unknown axis: %s') % axis)
		idx = names.index(axis)
		macro_no = int(self.macro_no or 0) + idx
		dec = int(self.macro_decimals or 4)
		_logger.info('jog _jog_axis device=%s axis=%s macro=%s value=%s decimals=%s', self.id, axis, macro_no, value, dec)
		# Probe the library for direct jog capabilities without connecting
		# to the controller. Constructing FocasClient only loads the shared
		# library and signatures; it does not call `connect()` unless entered
		# as a context manager. We report candidates back to the caller and do
		# NOT invoke native jog symbols automatically to avoid crashes.
		try:
			focas = FocasClient(self.host, self.port, self.timeout)
			cands = focas.probe_jog_symbols()
			if cands:
				_logger.info('focas jog candidates for device %s: %s', self.id, cands)
				return {'candidates': cands}
		except Exception:
			_logger.exception('failed to probe focas jog symbols for device %s', self.id)

		# Fallback: Use Focas write_macro to set the macro value. Only attempt
		# this if a valid base macro number is configured on the device.
		if int(self.macro_no or 0) <= 0:
			raise UserError(_('No macro base configured for device; set `Macro Number` > 0, or use a direct FOCAS jog function.'))
		try:
			with FocasClient(self.host, self.port, self.timeout) as focas:
				focas.write_macro(macro_no, value, dec)
				# read back the macro to verify it was set
				try:
					read_back = focas.read_macro(macro_no)
					_logger.info('jog read_back device=%s macro=%s read_back=%s', self.id, macro_no, read_back)
				except Exception:
					_logger.exception('failed to read_macro back for device %s macro %s', self.id, macro_no)
					read_back = None
		except Exception:
			_logger.exception('failed to write_macro for device %s macro %s', self.id, macro_no)
			raise
		return read_back

	def _register_hook(self):
		# Start the background updater when the registry is ready.
		try:
			_start_background_updater(self.env)
		except Exception:
			# Do not prevent registry setup on failure to start thread
			pass
		return super(MachineControlCncDevice, self)._register_hook()


# Background updater thread: read all active devices frequently
_updater_started = False

def _background_updater(registry):
	# Runs in a daemon thread. Use a fresh DB cursor and Environment per
	# iteration to avoid sharing ORM env/cursor between threads.
	interval = 1
	_logger.info('mc_live_updater thread started, interval=%s', interval)
	while True:
		try:
			with registry.cursor() as cr:
				env = api.Environment(cr, SUPERUSER_ID, {})
				devices = env['machine_control.cnc.device'].search([('active', '=', True)])
				for dev in devices:
					# sample the device without writing the main device row
					res = None
					try:
						res = env['machine_control.cnc.device'].browse(dev.id).sample_position()
					except Exception:
						_logger.exception('error sampling device %s', dev.id)
						continue
					if not res or res.get('status') != 'ok':
						# sampling failed for this device; skip
						continue
					payload = res.get('payload')
					ts = fields.Datetime.now()
					# persist a lightweight live row to avoid updating the same device row
					try:
						env['machine_control.cnc.live'].create({'device_id': dev.id, 'ts': ts, 'payload': json.dumps(payload, default=str)})
					except Exception:
						_logger.exception('failed to create live row for device %s', dev.id)
						continue
					# push update to connected clients via the bus
					try:
						env['bus.bus']._sendone(
							'machine_control.live.%d' % dev.id,
							'machine_control.live_update',
							{
								'status': 'ok',
								'last_read_at': str(ts),
								'payload': payload,
							},
						)
					except Exception:
						_logger.exception('failed to send bus notification for device %s', dev.id)
		except Exception:
			# swallow unexpected exceptions but keep thread alive
			_logger.exception('mc_live_updater unexpected error')
		time.sleep(interval)


def _start_background_updater(env):
	global _updater_started
	if _updater_started:
		return
	_updater_started = True
	_logger.info('Starting mc_live_updater thread')
	# pass registry to the background thread which will open its own cursors
	thread = threading.Thread(target=_background_updater, args=(env.registry,), daemon=True, name='mc_live_updater')
	thread.start()


class MachineControlCncLive(models.Model):
	_name = 'machine_control.cnc.live'
	_description = 'Live CNC position samples'

	device_id = fields.Many2one('machine_control.cnc.device', required=True, ondelete='cascade', index=True)
	ts = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
	payload = fields.Text()
