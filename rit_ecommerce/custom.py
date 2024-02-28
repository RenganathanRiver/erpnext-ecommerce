import frappe

@frappe.whitelist()
def get_variant_url(item_code):
    try:
        route = frappe.get_value("Website Item", {"item_code": item_code}, "route")
        if route:
            return frappe.utils.get_url(route)
    except frappe.DoesNotExistError:
        frappe.throw(f"Item not created")

    except Exception as e:
        frappe.throw(f"Error fetching variant URL: {e}")
    return None