# -*- coding: utf-8 -*-
{
    'name': "machine_control",

    'summary': "Read FANUC CNC Control",

    'description': """
        Configure FANUC CNC controllers and read live axis position data and controll the machine
        using the installed pyfwlib/fwlib Python extension.
    """,

    'author': "Simeon Hege",
    'website': "http://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Productivity',
    'version': '15.0.0.0.0',
    'license': 'LGPL-3',

    # any module necessary for this one to work correctly
    'depends': ['base'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}
