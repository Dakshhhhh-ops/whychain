"""The order-level fact model of each dataset: one federated query, the delivery stages and the dimensions.

Every dataset is described the same way, so the same analyses work on both:

  purchase  ->  handover to the carrier  ->  delivery        (and a promised delivery date)
  handover days = handover - purchase      (seller or warehouse)
  transit days  = delivery - handover      (carrier)
  promised days = promise - purchase       (what the customer was told)
  late          = delivered on a later calendar date than promised   (i.e. handover + transit > promised)
"""
from __future__ import annotations

from dataclasses import dataclass, field

BR_REGION = {
    "AC": "North", "AM": "North", "AP": "North", "PA": "North", "RO": "North", "RR": "North", "TO": "North",
    "AL": "Northeast", "BA": "Northeast", "CE": "Northeast", "MA": "Northeast", "PB": "Northeast", "PE": "Northeast",
    "PI": "Northeast", "RN": "Northeast", "SE": "Northeast",
    "DF": "Centre-West", "GO": "Centre-West", "MS": "Centre-West", "MT": "Centre-West",
    "ES": "Southeast", "MG": "Southeast", "RJ": "Southeast", "SP": "Southeast",
    "PR": "South", "RS": "South", "SC": "South",
}

STAGES = {
    "handover": "hand-over to the carrier (seller or warehouse)",
    "transit": "carrier transit",
    "promise": "promised delivery window",
}


@dataclass(frozen=True)
class FactModel:
    dataset: str
    sql: str                                   # Spark SQL, one row per order (duplicates are dropped by order_id)
    delivered_status: tuple[str, ...]          # statuses that count as delivered
    dims: dict[str, str]                       # column -> label, for segment breakdowns
    map_within: str                            # stages are compared within this dimension (like with like)
    region_map: dict[str, str] = field(default_factory=dict)   # map_within value -> region (adds a "region" dim)
    region_source: str = ""                    # the column region_map applies to
    reviews_sql: str = ""                      # optional: order_id, review text, for text mentions
    stock_sql: str = ""                        # optional: one row per order line with the warehouse's stock of that
                                               # SKU on the order's requested ship date (datasets with stock data)
    currency: str = "BRL"
    unit: str = "orders"


OLIST = FactModel(
    dataset="olist",
    sql="""SELECT o.order_id, o.order_status AS status,
       o.order_purchase_timestamp AS purchase_ts, o.order_delivered_carrier_date AS handover_ts,
       o.order_delivered_customer_date AS delivered_ts, o.order_estimated_delivery_date AS promised_ts,
       c.customer_state, s.seller_state, i.seller_id, p.product_category_name_english AS category,
       i.n_items, i.price, i.freight
FROM oms.orders o
JOIN oms.customers c ON c.customer_id = o.customer_id
LEFT JOIN (SELECT order_id, MIN(seller_id) AS seller_id, MIN(product_id) AS product_id, COUNT(*) AS n_items,
                  SUM(price) AS price, SUM(freight_value) AS freight
           FROM sellers.order_items GROUP BY order_id) i ON i.order_id = o.order_id
LEFT JOIN sellers.sellers s ON s.seller_id = i.seller_id
LEFT JOIN sellers.products p ON p.product_id = i.product_id""",
    delivered_status=("delivered",),
    dims={"customer_state": "customer state", "region": "customer region", "seller_state": "seller state",
          "seller_region": "seller region", "interstate": "seller in another state", "category": "product category",
          "seller_id": "seller"},
    map_within="customer_state",
    region_map=BR_REGION,
    region_source="customer_state",
    reviews_sql="""SELECT order_id, review_id, review_score,
       COALESCE(review_comment_title, '') AS title, COALESCE(review_comment_message, '') AS message
FROM feedback.reviews""",
    currency="BRL",
)

KAVERI = FactModel(
    dataset="kaveri",
    sql="""SELECT o.order_id, o.status, o.order_date AS purchase_ts, s.pickup_date AS handover_ts,
       s.delivered_date AS delivered_ts, o.promised_date AS promised_ts,
       o.warehouse_id, s.dest_city, s.carrier_id, s.lane_id, s.service_type, o.channel, c.tier AS customer_tier,
       o.order_value_inr AS price
FROM erp.sales_orders o
JOIN erp.customers c ON c.customer_id = o.customer_id
LEFT JOIN carrier.shipments s ON s.order_id = o.order_id""",
    delivered_status=("DELIVERED",),
    dims={"dest_city": "destination city", "warehouse_id": "warehouse", "carrier_id": "carrier", "lane_id": "lane",
          "service_type": "service type", "channel": "channel", "customer_tier": "customer tier"},
    map_within="dest_city",
    currency="INR",
    stock_sql="""SELECT o.order_id, o.warehouse_id, o.order_date, o.requested_ship_date, o.promo_code, ol.sku, ol.qty,
       k.category, k.supplier_id, p.name AS supplier_name, i.on_hand
FROM erp.sales_orders o
JOIN erp.order_lines ol ON ol.order_id = o.order_id
JOIN erp.skus k ON k.sku = ol.sku
LEFT JOIN procurement.suppliers p ON p.supplier_id = k.supplier_id
LEFT JOIN wms.inventory_snapshots i ON i.warehouse_id = o.warehouse_id AND i.sku = ol.sku
  AND CAST(i.snapshot_date AS DATE) = CAST(o.requested_ship_date AS DATE)""",
)

MODELS = {"olist": OLIST, "kaveri": KAVERI}


def fact_model(dataset_key: str) -> FactModel | None:
    return MODELS.get(dataset_key)
