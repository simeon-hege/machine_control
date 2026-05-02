# -*- coding: utf-8 -*-

import logging

from odoo import fields, models, _

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

	def get_live_data(self):
		"""RPC method used by the OWL live view — always does a fresh read."""
		self.ensure_one()
		result = self.sudo().sample_position()
		ts = str(fields.Datetime.now())
		if result.get('status') == 'ok':
			return {'status': 'ok', 'last_read_at': ts, 'payload': result.get('payload')}
		return {'status': 'error', 'last_read_at': ts, 'error': result.get('error'), 'payload': None}





