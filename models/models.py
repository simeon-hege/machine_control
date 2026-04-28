# -*- coding: utf-8 -*-

import json
import logging
import fwlib

from odoo import fields, models, _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class MachineControlCncDevice(models.Model):
	_name = 'machine_control.cnc.device'
	_description = 'FANUC CNC Device'

	name = fields.Char(required=True)
	host = fields.Char(required=True, string="Machine IP", help='FANUC controller IP or hostname')
	port = fields.Integer(default=8193, required=True)
	timeout = fields.Integer(default=10, required=True, help='Connection timeout in seconds')
	active = fields.Boolean(default=True)

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

	def _normalize_axes(self, axes_data):
		normalized = []
		if not isinstance(axes_data, (list, tuple)):
			return normalized

		for axis in axes_data:
			if isinstance(axis, dict):
				normalized.append({
					'index': axis.get('index'),
					'id': axis.get('id'),
					'suffix': axis.get('suffix'),
					'divisor': axis.get('divisor'),
				})
			else:
				normalized.append({'value': axis})
		return normalized

	def _invoke_reader(self, fwlib, method_name):
		method = getattr(fwlib, method_name, None)
		if not callable(method):
			return None

		call_patterns = [(), (0,), (1,), (0, 0)]
		last_error = None
		for args in call_patterns:
			try:
				return {
					'method': method_name,
					'args': list(args),
					'data': method(*args),
				}
			except TypeError as exc:
				last_error = str(exc)
				continue
			except Exception as exc:  # pragma: no cover - hardware/runtime dependent
				last_error = str(exc)
				break

		if last_error:
			_logger.info('Unable to read with %s: %s', method_name, last_error)
		return None

	def _read_position_payload(self, fwlib):
		for method_name in ('rdposition', 'position', 'absolute', 'machine', 'relative'):
			payload = self._invoke_reader(fwlib, method_name)
			if payload is not None:
				return payload

		grouped = {}
		for method_name in ('absolute', 'machine', 'relative'):
			payload = self._invoke_reader(fwlib, method_name)
			if payload is not None:
				grouped[method_name] = payload
		if grouped:
			return {'method': 'grouped', 'data': grouped}

		available = sorted(name for name in dir(fwlib) if not name.startswith('_'))
		raise UserError(_(
			'No supported position reader is exposed by the installed pyfwlib/fwlib module. '
			'Expected one of: rdposition, position, absolute, machine, relative. Available methods: %s'
		) % ', '.join(available))

	def _create_snapshot(self, status, payload=None, error_message=None):
		self.env['machine_control.cnc.snapshot'].create({
			'device_id': self.id,
			'read_at': fields.Datetime.now(),
			'status': status,
			'error_message': error_message,
			'payload': self._serialize_payload(payload) if payload else False,
		})

	def action_read_position(self):
		self.ensure_one()

		connected = False
		try:
			fwlib.allclibhndl3(self.host, int(self.port), int(self.timeout))
			connected = True

			sysinfo = fwlib.sysinfo()
			cnc_id = fwlib.rdcncid()
			axes_data = fwlib.rdaxisname()
			position_payload = self._read_position_payload(fwlib)

			payload = {
				'device': self.name,
				'host': self.host,
				'port': self.port,
				'sysinfo': sysinfo,
				'cnc_id': cnc_id,
				'axes': self._normalize_axes(axes_data),
				'position': position_payload,
			}

			serialized = self._serialize_payload(payload)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'ok',
				'last_error': False,
				'last_position_data': serialized,
			})
			self._create_snapshot('ok', payload=payload)
		except Exception as exc:
			message = str(exc)
			self.write({
				'last_read_at': fields.Datetime.now(),
				'last_status': 'error',
				'last_error': message,
			})
			self._create_snapshot('error', error_message=message)
			raise UserError(_('Failed to read FANUC position data: %s') % message) from exc
		finally:
			if connected and hasattr(fwlib, 'freelibhndl'):
				try:
					fwlib.freelibhndl()
				except Exception:
					_logger.info('Failed to release fwlib handle cleanly.', exc_info=True)

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
