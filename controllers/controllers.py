# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response
import json
import time
import logging

_logger = logging.getLogger(__name__)


class MachineControl(http.Controller):
	@http.route('/machine_control/device/<int:device_id>/live', auth='user', type='http')
	def live_view(self, device_id, **kw):
		device = request.env['machine_control.cnc.device'].sudo().browse(device_id)
		if not device.exists():
			return Response(json.dumps({'error': 'device not found'}), status=404, mimetype='application/json')
		try:
			return request.render('machine_control.live_view', {'device_id': device_id, 'device': device})
		except Exception:
			# Let the error propagate — template should be present after module update
			raise

	@http.route('/machine_control/api/position/<int:device_id>', auth='user', type='http', methods=['GET'])
	def api_position(self, device_id, **kw):
		"""Return the current last position data as JSON. If the query
		parameter `refresh=1` is provided the controller will perform a
		live read (without creating a snapshot) and return the fresh data.
		"""
		refresh = kw.get('refresh') in ('1', 'true', 'True')
		device = request.env['machine_control.cnc.device'].sudo().browse(device_id)
		if not device.exists():
			return Response(json.dumps({'error': 'device not found'}), status=404, mimetype='application/json')

		if refresh:
			result = device.sudo().read_position_no_snapshot()
			return Response(json.dumps(result, default=str), mimetype='application/json')

		# return last stored position
		data = device.last_position_data
		try:
			payload = json.loads(data) if data else None
		except Exception:
			payload = data
		out = {
			'status': device.last_status,
			'last_read_at': str(device.last_read_at) if device.last_read_at else None,
			'payload': payload,
		}
		return Response(json.dumps(out, default=str), mimetype='application/json')

	@http.route('/machine_control/api/snapshot/<int:device_id>', auth='user', type='http', methods=['POST','GET'])
	def api_snapshot(self, device_id, **kw):
		"""Trigger a fresh read and create a snapshot. Returns simple JSON
		acknowledging success or reporting error.
		"""
		device = request.env['machine_control.cnc.device'].sudo().browse(device_id)
		if not device.exists():
			return Response(json.dumps({'error': 'device not found'}), status=404, mimetype='application/json')
		try:
			device.sudo().action_read_position()
			return Response(json.dumps({'result': 'ok'}), mimetype='application/json')
		except Exception as exc:
			return Response(json.dumps({'result': 'error', 'error': str(exc)}), mimetype='application/json')

	@http.route('/machine_control/api/longpoll/<int:device_id>', auth='user', type='http', methods=['GET'])
	def api_longpoll(self, device_id, **kw):
		"""Long-polling endpoint that waits until the device fields change.
		Query parameter `last` should contain the client's last seen `last_read_at` value.
		The controller will hold the connection up to `timeout` seconds (default 30s)
		and return when a new read is available or when the timeout elapses.
		"""
		last_seen = kw.get('last')
		timeout = float(kw.get('timeout', 30.0))
		poll_interval = float(kw.get('interval', 0.01))

		_logger.debug('api_longpoll enter device=%s last=%s timeout=%s interval=%s', device_id, last_seen, timeout, poll_interval)

		device = request.env['machine_control.cnc.device'].sudo().browse(device_id)
		if not device.exists():
			return Response(json.dumps({'error': 'device not found'}), status=404, mimetype='application/json')

		start = time.time()
		# Read latest live sample if available, else fall back to device fields
		def _get_latest():
			live = request.env['machine_control.cnc.live'].sudo().search([('device_id', '=', device_id)], order='ts desc', limit=1)
			if live:
				return (str(live.ts), json.loads(live.payload) if live.payload else None)
			# fallback to device row
			return (str(device.last_read_at) if device.last_read_at else None, json.loads(device.last_position_data) if device.last_position_data else None)

		current, payload = _get_latest()
		if last_seen != current:
			data = {
				'status': device.last_status,
				'last_read_at': current,
				'payload': payload,
			}
			_logger.debug('api_longpoll immediate return device=%s last_read_at=%s', device_id, current)
			return Response(json.dumps(data, default=str), mimetype='application/json')

		# wait loop
		while time.time() - start < timeout:
			# sleep a short while and re-check
			time.sleep(poll_interval)
			current, payload = _get_latest()
			if last_seen != current:
				data = {
					'status': device.last_status,
					'last_read_at': current,
					'payload': payload,
				}
				_logger.debug('api_longpoll returning updated device=%s last_read_at=%s', device_id, current)
				return Response(json.dumps(data, default=str), mimetype='application/json')

		# timed out, return current state (may be same as before)
		data = {
			'status': device.last_status,
			'last_read_at': current,
			'payload': json.loads(device.last_position_data) if device.last_position_data else None,
		}
		_logger.debug('api_longpoll timeout returning device=%s last_read_at=%s', device_id, current)
		return Response(json.dumps(data, default=str), mimetype='application/json')

	@http.route('/machine_control/api/jog/<int:device_id>', auth='user', type='json', methods=['POST'], csrf=False)
	def api_jog(self, device_id, **kw):
		"""JSON endpoint to jog an axis. Expects JSON body: {'axis': 'x', 'value': 0.1}
		Returns a simple dict which Odoo will serialize to JSON.
		"""
		try:
			data = request.jsonrequest or {}
			axis = data.get('axis')
			value = data.get('value')
			_logger.debug('api_jog request device=%s body=%s', device_id, data)
			if axis is None or value is None:
				return {'result': 'error', 'error': 'axis and value required'}
			device = request.env['machine_control.cnc.device'].sudo().browse(device_id)
			if not device.exists():
				return {'result': 'error', 'error': 'device not found'}
			try:
				res = device.sudo()._jog_axis(axis, value)
				_logger.info('api_jog succeeded device=%s axis=%s value=%s res=%s', device_id, axis, value, bool(res))
				# If the model returned candidate symbols, include them in the response
				if isinstance(res, dict) and 'candidates' in res:
					return {'result': 'ok', 'candidates': res['candidates']}
				return {'result': 'ok', 'read_back': res}
			except Exception as exc:
				_logger.exception('jog failed for device=%s axis=%s', device_id, axis)
				return {'result': 'error', 'error': str(exc)}
		except Exception as exc:
			_logger.exception('unexpected error in api_jog for device=%s', device_id)
			return {'result': 'error', 'error': 'internal', 'detail': str(exc)}
