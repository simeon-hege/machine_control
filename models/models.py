# -*- coding: utf-8 -*-

import json
import logging

from odoo import fields, models, _
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
				print('-----------------------------------------')
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
