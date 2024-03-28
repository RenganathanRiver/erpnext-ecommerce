import frappe
from erpnext.accounts.party import get_party_account, get_party_bank_account
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
)
from frappe import _
from frappe.utils import flt, nowdate
from erpnext.accounts.doctype.payment_request.payment_request import get_gateway_details


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

@frappe.whitelist(allow_guest=True)
def make_payment_request(**args):
	"""Make payment request"""

	args = frappe._dict(args)

	ref_doc = frappe.get_doc(args.dt, args.dn)
	gateway_account = get_gateway_details(args) or frappe._dict()

	grand_total = get_amount(ref_doc, gateway_account.get("payment_account"))
	if args.loyalty_points and args.dt == "Sales Order":
		from erpnext.accounts.doctype.loyalty_program.loyalty_program import validate_loyalty_points

		loyalty_amount = validate_loyalty_points(ref_doc, int(args.loyalty_points))
		frappe.db.set_value(
			"Sales Order", args.dn, "loyalty_points", int(args.loyalty_points), update_modified=False
		)
		frappe.db.set_value(
			"Sales Order", args.dn, "loyalty_amount", loyalty_amount, update_modified=False
		)
		grand_total = grand_total - loyalty_amount

	bank_account = (
		get_party_bank_account(args.get("party_type"), args.get("party"))
		if args.get("party_type")
		else ""
	)

	draft_payment_request = frappe.db.get_value(
		"Payment Request",
		{"reference_doctype": args.dt, "reference_name": args.dn, "docstatus": 0},
	)

	existing_payment_request_amount = get_existing_payment_request_amount(args.dt, args.dn)

	if existing_payment_request_amount:
		grand_total -= existing_payment_request_amount

	if draft_payment_request:
		frappe.db.set_value(
			"Payment Request", draft_payment_request, "grand_total", grand_total, update_modified=False
		)
		pr = frappe.get_doc("Payment Request", draft_payment_request)
	else:
		pr = frappe.new_doc("Payment Request")
		pr.update(
			{
				"payment_gateway_account": gateway_account.get("name"),
				"payment_gateway": gateway_account.get("payment_gateway"),
				"payment_account": gateway_account.get("payment_account"),
				"payment_channel": gateway_account.get("payment_channel"),
				"payment_request_type": args.get("payment_request_type"),
				"currency": ref_doc.currency,
				"grand_total": grand_total,
				"mode_of_payment": args.mode_of_payment,
				"email_to": args.recipient_id or ref_doc.owner,
				"subject": _("Payment Request for {0}").format(args.dn),
				"message": gateway_account.get("message") or get_dummy_message(ref_doc),
				"reference_doctype": args.dt,
				"reference_name": args.dn,
				"party_type": args.get("party_type") or "Customer",
				"party": args.get("party") or ref_doc.get("customer"),
				"bank_account": bank_account,
			}
		)

		# Update dimensions
		pr.update(
			{
				"cost_center": ref_doc.get("cost_center"),
				"project": ref_doc.get("project"),
			}
		)

		for dimension in get_accounting_dimensions():
			pr.update({dimension: ref_doc.get(dimension)})

		if args.order_type == "Shopping Cart" or args.mute_email:
			pr.flags.mute_email = True

		pr.insert(ignore_permissions=True)
		if args.submit_doc:
			pr.submit()

	if args.order_type == "Shopping Cart":
		frappe.db.commit()
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = pr.get_payment_url()

	if args.return_doc:
		return pr

	return pr.as_dict()


@frappe.whitelist(allow_guest=True)
def make_partial_payment_request(**args):
	"""Make Partial payment request"""

	args = frappe._dict(args)

	ref_doc = frappe.get_doc(args.dt, args.dn)
	gateway_account = get_gateway_details(args) or frappe._dict()

	grand_total = get_amount(ref_doc, gateway_account.get("payment_account"))

	bank_account = (
		get_party_bank_account(args.get("party_type"), args.get("party"))
		if args.get("party_type")
		else ""
	)

	grand_total = float(args.get("partial_amount"))

	pr = frappe.new_doc("Payment Request")
	pr.update(
		{
			"payment_gateway_account": gateway_account.get("name"),
			"payment_gateway": gateway_account.get("payment_gateway"),
			"payment_account": gateway_account.get("payment_account"),
			"payment_channel": gateway_account.get("payment_channel"),
			"payment_request_type": args.get("payment_request_type"),
			"currency": ref_doc.currency,
			"grand_total": grand_total,
			"mode_of_payment": args.mode_of_payment,
			"email_to": args.recipient_id or ref_doc.owner,
			"subject": _("Payment Request for {0}").format(args.dn),
			"message": gateway_account.get("message") or get_dummy_message(ref_doc),
			"reference_doctype": args.dt,
			"reference_name": args.dn,
			"party_type": args.get("party_type") or "Customer",
			"party": args.get("party") or ref_doc.get("customer"),
			"bank_account": bank_account,
		}
	)

	# Update dimensions
	pr.update(
		{
			"cost_center": ref_doc.get("cost_center"),
			"project": ref_doc.get("project"),
		}
	)

	for dimension in get_accounting_dimensions():
		pr.update({dimension: ref_doc.get(dimension)})

	pr.flags.mute_email = True

	pr.insert(ignore_permissions=True)
	if args.submit_doc:
		pr.submit()

	frappe.db.commit()
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = pr.get_payment_url()

	if args.return_doc:
		return pr

	return pr.as_dict()

def get_amount(ref_doc, payment_account=None):
	"""get amount based on doctype"""
	dt = ref_doc.doctype
	if dt in ["Sales Order", "Purchase Order"]:
		grand_total = flt(ref_doc.rounded_total) or flt(ref_doc.grand_total)
	elif dt in ["Sales Invoice", "Purchase Invoice"]:
		if not ref_doc.get("is_pos"):
			if ref_doc.party_account_currency == ref_doc.currency:
				grand_total = flt(ref_doc.outstanding_amount)
			else:
				grand_total = flt(ref_doc.outstanding_amount) / ref_doc.conversion_rate
		elif dt == "Sales Invoice":
			for pay in ref_doc.payments:
				if pay.type == "Phone" and pay.account == payment_account:
					grand_total = pay.amount
					break
	elif dt == "POS Invoice":
		for pay in ref_doc.payments:
			if pay.type == "Phone" and pay.account == payment_account:
				grand_total = pay.amount
				break
	elif dt == "Fees":
		grand_total = ref_doc.outstanding_amount

	if grand_total > 0:
		return grand_total
	else:
		frappe.throw(_("Payment Entry is already created"))
		


def get_existing_payment_request_amount(ref_dt, ref_dn):
	"""
	Get the existing payment request which are unpaid or partially paid for payment channel other than Phone
	and get the summation of existing paid payment request for Phone payment channel.
	"""
	existing_payment_request_amount = frappe.db.sql(
		"""
		select sum(grand_total)
		from `tabPayment Request`
		where
			reference_doctype = %s
			and reference_name = %s
			and docstatus = 1
			and (status != 'Paid'
			or (payment_channel = 'Phone'
				and status = 'Paid'))
	""",
		(ref_dt, ref_dn),
	)
	return flt(existing_payment_request_amount[0][0]) if existing_payment_request_amount else 0




def get_dummy_message(doc):
	return frappe.render_template(
		"""{% if doc.contact_person -%}
<p>Dear {{ doc.contact_person }},</p>
{%- else %}<p>Hello,</p>{% endif %}

<p>{{ _("Requesting payment against {0} {1} for amount {2}").format(doc.doctype,
	doc.name, doc.get_formatted("grand_total")) }}</p>

<a href="{{ payment_url }}">{{ _("Make Payment") }}</a>

<p>{{ _("If you have any questions, please get back to us.") }}</p>

<p>{{ _("Thank you for your business!") }}</p>
""",
		dict(doc=doc, payment_url="{{ payment_url }}"),
	)

def get_payment_gateway_account(args):
	return frappe.db.get_value(
		"Payment Gateway Account",
		args,
		["name", "payment_gateway", "payment_account", "message"],
		as_dict=1,
	)
