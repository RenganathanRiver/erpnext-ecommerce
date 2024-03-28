// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ready(() => {
	var loyalty_points_input = document.getElementById("loyalty-point-to-redeem");
	var loyalty_points_status = document.getElementById("loyalty-points-status");

	if (loyalty_points_input) {
		loyalty_points_input.onblur = apply_loyalty_points;
	}

	function apply_loyalty_points() {
		var loyalty_points = parseInt(loyalty_points_input.value);

		if (!loyalty_points) return;

		const callback = async (r) => {
			if (!r) return;

			var message = ""
			let loyalty_amount = flt(r.message * loyalty_points);

			if (doc_info.grand_total && doc_info.grand_total < loyalty_amount) {
				let redeemable_amount = parseInt(doc_info.grand_total/r.message);
				message = "You can only redeem max " + redeemable_amount + " points in this order.";
				frappe.msgprint(__(message));
				return;
			}

			message = loyalty_points + " Loyalty Points of amount "+ loyalty_amount + " is applied."
			loyalty_points_status.innerHTML = message;
			frappe.msgprint(__(message));

			const args_obj = {
				dn: doc_info.doctype_name,
				dt: doc_info.doctype,
				submit_doc: 1,
				order_type: "Shopping Cart",
				loyalty_points,
			}

			const payment_gateway_account = await frappe.db.get_single_value('Webshop Settings', 'payment_gateway_account')

			if (payment_gateway_account) {
				args_obj.payment_gateway_account = payment_gateway_account;
			}

			const args_str = Object
				.entries(args_obj)
				.map((e) => e[0] + "=" + e[1])
				.join("&");

			const href_base_url = "/api/method/erpnext.accounts.doctype.payment_request.payment_request.make_payment_request"
			const href = href_base_url + "?" + args_str;

			var payment_button = document.getElementById("pay-for-order");
			payment_button.innerHTML = __("Pay Remaining");
			payment_button.href = href;
		}

		frappe.call({
			method: "erpnext.accounts.doctype.loyalty_program.loyalty_program.get_redeemption_factor",
			args: {
				"customer": doc_info.customer
			},
			callback,
		});
	}
})

const partial_amount_field = document.getElementById('partial');
const pay_partial_button = document.getElementById('pay-partial-amount');
const pay_for_order_button = document.getElementById('pay-for-order');

pay_partial_button.addEventListener('click', function() {
    const partial_amount = parseFloat(partial_amount_field.value);
	const payment_amount_text = pay_for_order_button.innerText.trim();
	const payment_amount = parseFloat(payment_amount_text.replace(/[^0-9.]/g, ''));
	if (partial_amount_field.style.display === 'block') {
        if (partial_amount > payment_amount) {
            alert("Error: Partial amount cannot exceed the total amount.");
            return;
        }
        window.location.href = '/api/method/rit_ecommerce.custom.make_partial_payment_request?dn={{ doc.name }}&dt={{ doc.doctype }}&submit_doc=1&partial_amount=' + encodeURIComponent(partial_amount);
    }
    partial_amount_field.style.display = partial_amount_field.style.display === 'none' ? 'block' : 'none';
    pay_for_order_button.style.display = 'none';
});
