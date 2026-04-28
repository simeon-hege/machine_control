# -*- coding: utf-8 -*-
# from odoo import http


# class MachineControl(http.Controller):
#     @http.route('/machine_control/machine_control', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/machine_control/machine_control/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('machine_control.listing', {
#             'root': '/machine_control/machine_control',
#             'objects': http.request.env['machine_control.machine_control'].search([]),
#         })

#     @http.route('/machine_control/machine_control/objects/<model("machine_control.machine_control"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('machine_control.object', {
#             'object': obj
#         })
