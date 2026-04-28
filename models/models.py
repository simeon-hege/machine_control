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

	snapshot_ids = fields.One2many('machine_control.cnc.snapshot', 'device_id', string='Snapshots', readonly=True)

	def _serialize_payload(self, payload):
		return json.dumps(payload, indent=2, sort_keys=True, default=str)

	def _create_snapshot(self, status, payload=None, error_message=None):
		vals = {
			'device_id': self.id,
			'read_at': fields.Datetime.now(),
			'status': status,
			'error_message': error_message,
			'payload': self._serialize_payload(payload) if payload else False,
		}

		# If position payload is present and OK, extract axis coordinates
		# into separate fields (absolute/relative/machine for x,y,z,a).
		try:
			if payload and 'position' in payload and 'data' in payload['position']:
				axis_list = payload['position']['data']
				names = ['x', 'y', 'z', 'a']
				for axis in axis_list[:4]:
					idx = int(axis.get('axis_index', 0))
					if idx < 0 or idx >= len(names):
						continue
					n = names[idx]
					# Coerce values to numbers or False to avoid assignment errors
					_abs = axis.get('absolute') or {}
					_rel = axis.get('relative') or {}
					_mac = axis.get('machine') or {}
					_abs_val = _abs.get('value')
					_rel_val = _rel.get('value')
					_mac_val = _mac.get('value')
					# If the library returned None or an implausible float, try to
					# reconstruct the value from raw and dec fields using Decimal
					from decimal import Decimal, InvalidOperation
					def compute_from_raw(dct):
						r = dct.get('raw')
						d = dct.get('dec')
						if r is None or d is None:
							return None
						try:
							return float(Decimal(int(r)) / (Decimal(10) ** Decimal(int(d))))
						except (InvalidOperation, OverflowError, ValueError):
							return None

					if _abs_val is None:
						_abs_val = compute_from_raw(_abs)
					if _rel_val is None:
						_rel_val = compute_from_raw(_rel)
					if _mac_val is None:
						_mac_val = compute_from_raw(_mac)
					vals[f'absolute_{n}'] = _abs_val if _abs_val is not None else False
					vals[f'relative_{n}'] = _rel_val if _rel_val is not None else False
					vals[f'machine_{n}'] = _mac_val if _mac_val is not None else False
		except Exception:
			# Do not fail snapshot creation on unexpected payload shape
			pass

		self.env['machine_control.cnc.snapshot'].create(vals)

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
			self._create_snapshot('ok', payload=payload)
		except (FocasError, OSError) as exc:
			message = str(exc)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'error',
				'last_error': message,
			})
			self._create_snapshot('error', error_message=message)
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
			self._create_snapshot('ok', payload=payload)
		except (FocasError, OSError) as exc:
			message = str(exc)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'error',
				'last_error': message,
			})
			self._create_snapshot('error', error_message=message)
			raise UserError(_('Failed to write FANUC macro: %s') % message) from exc

		return True

	def action_open_live(self):
		"""Return an action that opens the live view for this device in a new tab."""
		self.ensure_one()
		return {
			'type': 'ir.actions.act_url',
			'url': '/machine_control/device/%s/live' % (self.id,),
			'target': 'new',
		}

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
					# persist a lightweight live row to avoid updating the same device row
					try:
						env['machine_control.cnc.live'].create({'device_id': dev.id, 'ts': fields.Datetime.now(), 'payload': json.dumps(payload, default=str)})
					except Exception:
						_logger.exception('failed to create live row for device %s', dev.id)
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


class MachineControlCncSnapshot(models.Model):
	_name = 'machine_control.cnc.snapshot'
	_description = 'FANUC CNC Position Snapshot'
	_order = 'read_at desc, id desc'

	device_id = fields.Many2one('machine_control.cnc.device', required=True, ondelete='cascade', index=True)
	read_at = fields.Datetime(required=True, default=fields.Datetime.now)
	status = fields.Selection([
		('ok', 'OK'),
		('error', 'Error'),
	], required=True)
	error_message = fields.Text()
	payload = fields.Text()

	# Separate coordinate fields for easier display and searching
	absolute_x = fields.Float(string='Absolute X', digits=(16, 6))
	absolute_y = fields.Float(string='Absolute Y', digits=(16, 6))
	absolute_z = fields.Float(string='Absolute Z', digits=(16, 6))
	absolute_a = fields.Float(string='Absolute A', digits=(16, 6))

	relative_x = fields.Float(string='Relative X', digits=(16, 6))
	relative_y = fields.Float(string='Relative Y', digits=(16, 6))
	relative_z = fields.Float(string='Relative Z', digits=(16, 6))
	relative_a = fields.Float(string='Relative A', digits=(16, 6))

	machine_x = fields.Float(string='Machine X', digits=(16, 6))
	machine_y = fields.Float(string='Machine Y', digits=(16, 6))
	machine_z = fields.Float(string='Machine Z', digits=(16, 6))
	machine_a = fields.Float(string='Machine A', digits=(16, 6))


class MachineControlCncLive(models.Model):
	_name = 'machine_control.cnc.live'
	_description = 'Live CNC position samples'

	device_id = fields.Many2one('machine_control.cnc.device', required=True, ondelete='cascade', index=True)
	ts = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
	payload = fields.Text()
