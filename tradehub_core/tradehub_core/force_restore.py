import os
import json
import frappe

def run():
    print("--- Force Restore V2 ---")
    bench_dir = '/home/ali/Masaüstü/İstoç Güncel Proje/Frappe_Mock_Data/Frappe_Marketplace/frappe-bench'
    
    # Clean old ones
    targets = ['TradeHub', 'TR Tradehub']
    for t in targets:
        if frappe.db.exists('Workspace', t):
            frappe.delete_doc('Workspace', t, ignore_permissions=True, force=True)
    frappe.db.commit()

    # Main Hub
    main_json = os.path.join(bench_dir, 'apps/tradehub_core/tradehub_core/tradehub_core/workspace/tr_tradehub/tr_tradehub.json')
    if os.path.exists(main_json):
        with open(main_json, 'r') as f:
            data = json.load(f)
        data['name'] = 'TR Tradehub'
        data['label'] = 'TR Tradehub'
        data['public'] = 1
        doc = frappe.get_doc({'doctype': 'Workspace', **data})
        doc.flags.ignore_permissions = True
        doc.insert()
        frappe.db.commit()
        print("  ✓ TR Tradehub imported")
    else:
        print(f"  X {main_json} not found")

    # Children
    apps = ['tradehub_catalog', 'tradehub_commerce', 'tradehub_compliance', 'tradehub_core', 'tradehub_logistics', 'tradehub_marketing', 'tradehub_seller']
    for a in apps:
        if frappe.db.exists('Workspace', a): # Attempting a simpler name for child too
             pass
        
        path = os.path.join(bench_dir, f'apps/{a}/{a}/{a}/workspace/{a}/{a}.json')
        if os.path.exists(path):
            with open(path, 'r') as f:
                data = json.load(f)
            data['public'] = 1
            data['parent_page'] = 'TR Tradehub'
            doc = frappe.get_doc({'doctype': 'Workspace', **data})
            doc.flags.ignore_permissions = True
            if frappe.db.exists('Workspace', doc.name):
                frappe.delete_doc('Workspace', doc.name, ignore_permissions=True, force=True)
            doc.insert()
            frappe.db.commit()
            print(f"  ✓ {doc.name} imported")
            
    print("--- Finished ---")
