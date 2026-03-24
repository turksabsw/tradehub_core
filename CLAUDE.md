# TradeHub Marketplace — Proje Rehberi (CLAUDE.md)

Bu dosya Auto-Claude agent'larının her oturum başında okuduğu tek kaynak belgedir.
Projenin gerçek yapısını, kurallarını ve cross-app referanslarını içerir.

---

## 1. PROJE KİMLİĞİ

- **Platform:** B2B Marketplace (İstoç Ticaret Merkezi bazlı)
- **Framework:** Frappe v15 (Python 3.12 + Client-side JS/jQuery)
- **Bench dizini:** frappe-bench/
- **App sayısı:** 7 custom app
- **Mimari:** Katmanlı bağımlılık zinciri (aşağıda)

---

## 2. APP BAĞIMLILIK ZİNCİRİ

```
tradehub_core (TEMEL — bağımlılığı yok)
├── tradehub_catalog (PIM katmanı → core)
│   ├── tradehub_commerce (Sipariş/Ödeme → core + catalog)
│   │   ├── tradehub_logistics (Kargo → core + commerce)
│   │   └── tradehub_marketing (Pazarlama → core + catalog + commerce)
│   └── tradehub_seller (Satıcı → core + catalog)
└── tradehub_compliance (Uyum/Sözleşme → core)
```

**Kural:** Bir app, yalnızca required_apps listesindeki app'lerin DocType'larına Link verebilir.
Yukarı yönde referans YAPILAMAZ (ör: tradehub_core → tradehub_seller referans veremez).

---

## 3. APP DETAYLARI

---

### 3.1 tradehub_core
- **Açıklama:** Temel platform altyapısı — multi-tenant izolasyon, ECA rule engine, Buyer, Organization, KYC, Coğrafi veriler
- **App dizini:** apps/tradehub_core/tradehub_core/
- **Modül dizini:** apps/tradehub_core/tradehub_core/tradehub_core/
- **DocType dizini:** apps/tradehub_core/tradehub_core/tradehub_core/doctype/
- **Bağımlılıklar:** Yok (temel katman)

**Aktif hooks.py:**
- doc_events["*"] → before_insert + validate: tradehub_core.utils.tenant.validate_tenant
- doc_events["*"] → on_update + on_submit + on_cancel + after_insert + on_trash: tradehub_core.eca.dispatcher.evaluate_rules
- doc_events["Customer"] → ERPNext reverse sync (on_update, after_insert, on_trash)
- scheduler_events: Yok (henüz aktif değil)

**DocType'lar (30):** Tenant, Buyer Profile, Buyer Category (is_tree), Buyer Interest Category (child), Buyer Level, Buyer Level Benefit (child), Organization, Organization Member (child), Premium Buyer, KYC Profile, KYC Document, City, District, Neighborhood, Commercial Region, Phone Code, Address Item (child), Location Item (child), ECA Rule, ECA Rule Action (child), ECA Rule Condition (child), ECA Rule Log, ECA Action Template, Analytics Settings, ERPNext Integration Settings, Keycloak Settings, Import Job, Import Job Error (child), File Attachment (child), Notification Template

**Utility modüller:** tradehub_core/utils/tenant.py, tradehub_core/utils/erpnext_sync.py, tradehub_core/utils/seller_payout.py, tradehub_core/eca/dispatcher.py, tradehub_core/webhooks/erpnext_hooks.py, tradehub_core/permissions.py

---

### 3.2 tradehub_catalog
- **Açıklama:** Product Information Management (PIM) — Ürün, Kategori, Attribute, Brand, Media, Variant, Ranking
- **App dizini:** apps/tradehub_catalog/tradehub_catalog/
- **Modül dizini:** apps/tradehub_catalog/tradehub_catalog/tradehub_catalog/
- **DocType dizini:** apps/tradehub_catalog/tradehub_catalog/tradehub_catalog/doctype/
- **Bağımlılıklar:** ["tradehub_core"]

**Aktif hooks.py:**
- doc_events: Yok (Product on_update yorumda)
- scheduler_events: hourly → media_processor, daily → ranking

**DocType'lar (52):**
*Temel Katalog:* Product, Product Variant (JS), Category (is_tree, JS + tree JS), Product Category (child), Brand, Brand Gating, Sales Channel
*Attribute Sistemi:* Attribute (JS), Attribute Value (child), Attribute Set, Attribute Set Item (child), Attribute Label Override (child), Product Attribute, Product Attribute Group, Product Attribute Value (child)
*PIM:* PIM Attribute, PIM Attribute Group, PIM Attribute Option (child), PIM Product, PIM Product Attribute Value (child), PIM Product Category Link (child), PIM Product Class Field Value (child), PIM Product Description (child), PIM Product Media (child), PIM Product Price (child), PIM Product Relation (child), PIM Product Variant, PIM Variant Axis Value (child), PIM Variant Media (child)
*Product Class:* Product Class, Product Class Allowed Status (child), Product Class Attribute Group (child), Product Class Display Field (child), Product Class Field (child), Product Class Role Permission (child), Product Class Search Config (child), Product Family, Family Attribute (child), Family Default Value (child)
*Diğer:* Product Pricing Tier (child), Variant Axis, Media Asset, Media Library, Required Image Angle (child), SEO Meta (child), Filter Config, Category Display Schema (child), Channel Field Mapping (child), Completeness Rule (child), Translatable Attribute Flag (child), Ranking Weight Config, Virtual Category Rule

**Utility modüller:** tradehub_catalog/pim/api.py, pim/channel_export.py, pim/completeness.py, pim/erpnext_sync.py, pim/variant_generator.py, tasks.py

---

### 3.3 tradehub_commerce
- **Açıklama:** Sipariş, ödeme, teklif, komisyon ve escrow yönetimi
- **App dizini:** apps/tradehub_commerce/tradehub_commerce/
- **Modül dizini:** apps/tradehub_commerce/tradehub_commerce/tradehub_commerce/
- **DocType dizini:** apps/tradehub_commerce/tradehub_commerce/tradehub_commerce/doctype/
- **Bağımlılıklar:** ["tradehub_core", "tradehub_catalog"]

**Aktif hooks.py:**
- doc_events["Sales Order"] → ERPNext reverse sync (on_update, after_insert, on_trash)
- scheduler_events: daily → seller_payout

**DocType'lar (40):**
*Sipariş:* Order (JS), Order Item (child), Order Event, Marketplace Order (JS), Marketplace Order Item (child), Sub Order (JS), Sub Order Item (child)
*Sepet:* Cart (JS), Cart Item (child), Cart Line (child)
*Ödeme:* Payment Intent, Payment Installment (child), Payment Method, Payment Method Customer (child), Payment Plan, Account Action
*Komisyon/Escrow:* Commission Plan, Commission Plan Rate (child), Commission Rule (child), Escrow Account, Escrow Event (child)
*RFQ:* RFQ (JS), RFQ Item (child), RFQ Attachment (child), RFQ Message (child), RFQ Message Thread, RFQ NDA Link (JS), RFQ Quote (JS), RFQ Quote Item (child), RFQ Quote Link (child), RFQ Quote Revision (child), RFQ Target Category (child), RFQ Target Seller (child), RFQ View Log, Quotation, Quotation Item (child)
*Fiyat/Vergi:* Price Break (child), Incoterm Price (child), Tax Rate, Tax Rate Category (child)

**Utility modüller:** tradehub_commerce/rfq_utils/api.py, rfq_utils/nda_integration.py, rfq_utils/tasks.py, integrations/iyzico.py, integrations/paytr.py, webhooks/erpnext_hooks.py, tasks.py

---

### 3.4 tradehub_seller
- **Açıklama:** Satıcı yaşam döngüsü — profil, başvuru, performans, bakiye, mağaza, listeleme, SKU
- **App dizini:** apps/tradehub_seller/tradehub_seller/
- **Modül dizini:** apps/tradehub_seller/tradehub_seller/tradehub_seller/
- **DocType dizini:** apps/tradehub_seller/tradehub_seller/tradehub_seller/doctype/
- **Bağımlılıklar:** ["tradehub_core", "tradehub_catalog"]

**Aktif hooks.py:**
- doc_events["Supplier"] → ERPNext reverse sync (on_update, after_insert, on_trash)
- scheduler_events: hourly → buybox_rotation, daily → kpi_tasks + tier_tasks

**DocType'lar (33):**
*Profil:* Seller Profile (JS), Seller Store, Seller Application (JS), Seller Application Category (child), Seller Application Document (child), Seller Application History (child), Premium Seller
*Performans:* Seller KPI (JS), Seller Score, Seller Metrics, Seller Tier, Seller Level, Seller Level Benefit (child), Seller Badge
*Finansal:* Seller Balance, Seller Bank Account, Seller Certification
*Etiketleme:* Seller Tag, Seller Tag Assignment, Seller Tag Rule, Seller Tag Rule Condition (child)
*Listeleme/SKU:* Listing (JS), Listing Attribute Value (child), Listing Bulk Pricing Tier (child), Listing Image (child), Listing Variant (child), Listing Variant Attribute (child), Related Listing Product (child), SKU, SKU Product (JS), Buy Box Entry, KPI Template, KPI Template Item (child)

**Utility modüller:** tradehub_seller/seller_tags/rule_engine.py, seller_tags/seller_metrics.py, seller_tags/tasks.py, webhooks/erpnext_hooks.py, tasks.py

---

### 3.5 tradehub_logistics
- **Açıklama:** Kargo, teslimat, shipping rule, tracking
- **App dizini:** apps/tradehub_logistics/tradehub_logistics/
- **Modül dizini:** apps/tradehub_logistics/tradehub_logistics/tradehub_logistics/
- **DocType dizini:** apps/tradehub_logistics/tradehub_logistics/tradehub_logistics/doctype/
- **Bağımlılıklar:** ["tradehub_core", "tradehub_commerce"]

**Aktif hooks.py:**
- doc_events["Shipment"] → on_update + after_insert: tradehub_logistics.handlers
- scheduler_events: hourly → shipment_tracking

**DocType'lar (10):** Carrier, Logistics Provider, Marketplace Shipment (JS), Shipment, Shipping Rule, Shipping Zone, Shipping Zone Rate (child), Shipping Rate Tier (child), Lead Time, Tracking Event

**Utility modüller:** tradehub_logistics/handlers.py, tasks.py, integrations/carriers/aras.py, integrations/carriers/yurtici.py

---

### 3.6 tradehub_marketing
- **Açıklama:** Kampanya, kupon, grup alım, abonelik, toptan teklif, mağaza vitrin
- **App dizini:** apps/tradehub_marketing/tradehub_marketing/
- **Modül dizini:** apps/tradehub_marketing/tradehub_marketing/tradehub_marketing/
- **DocType dizini:** apps/tradehub_marketing/tradehub_marketing/tradehub_marketing/doctype/
- **Bağımlılıklar:** ["tradehub_core", "tradehub_catalog", "tradehub_commerce"]

**Aktif hooks.py:**
- doc_events["Campaign"] → on_update + after_insert: tradehub_marketing.handlers
- scheduler_events: daily → campaign_tasks

**DocType'lar (13):** Campaign, Coupon (JS), Coupon Category Item (child), Coupon Product Item (child), Group Buy, Group Buy Commitment (child), Group Buy Payment (child), Group Buy Tier (child), Storefront, Subscription, Subscription Package, Wholesale Offer, Wholesale Offer Product (child)

**Utility modüller:** tradehub_marketing/handlers.py, tasks.py, groupbuy/api.py, groupbuy/pricing.py, groupbuy/tasks.py

---

### 3.7 tradehub_compliance
- **Açıklama:** Uyum, sözleşme, onay, e-imza, review, moderasyon, risk skoru, mesajlaşma
- **App dizini:** apps/tradehub_compliance/tradehub_compliance/
- **Modül dizini:** apps/tradehub_compliance/tradehub_compliance/tradehub_compliance/
- **DocType dizini:** apps/tradehub_compliance/tradehub_compliance/tradehub_compliance/doctype/
- **Bağımlılıklar:** ["tradehub_core"]

**Aktif hooks.py:**
- doc_events: Yok (henüz aktif değil)
- scheduler_events: daily → certificate_alerts

**DocType'lar (25):** Certificate, Certificate Type, Contract Template, Contract Instance, Contract Revision, Contract Rule, Contract Rule Condition (child), Marketplace Contract Template, Marketplace Contract Instance, Consent Record, Marketplace Consent Record, Consent Topic, Consent Text, Consent Channel, Consent Method, Consent Audit Log, ESign Provider, ESign Transaction, Review (JS), Moderation Case, Risk Score, Risk Score Factor (child), Sample Request, Message, Message Thread

**Utility modüller:** tradehub_compliance/reviews/api.py, reviews/moderation.py, reviews/review_manager.py, reviews/tasks.py, tasks.py

---

## 4. CROSS-APP REFERANS HARİTASI

Bir app'te çalışırken başka app'lerin DocType'larına Link vermeniz gerektiğinde:

| Kaynak App | Hedef DocType | Hedef App | Kullanım |
|------------|--------------|-----------|----------|
| seller | Tenant | core | Seller → Tenant izolasyonu |
| seller | Category | catalog | Listing → Category bağlantısı |
| seller | Product | catalog | Listing → Product bağlantısı |
| commerce | Buyer Profile | core | Order → Buyer bağlantısı |
| commerce | Tenant | core | Order → Tenant izolasyonu |
| commerce | Product | catalog | Order Item → Product |
| commerce | Category | catalog | RFQ Target Category |
| logistics | Order / Sub Order | commerce | Shipment → Order bağlantısı |
| marketing | Product | catalog | Coupon Product Item → Product |
| marketing | Category | catalog | Coupon Category Item → Category |
| marketing | Order | commerce | Campaign satış etkisi |

**ERPNext DocType Referansları (Frappe/ERPNext core'dan):**
- Customer → tradehub_core dinliyor
- Supplier → tradehub_seller dinliyor
- Sales Order → tradehub_commerce dinliyor
- Shipment → tradehub_logistics dinliyor
- Country, Currency, Industry Type → Frappe core, tüm app'ler kullanabilir

---

## 5. FRAPPE GELİŞTİRME KURALLARI

### 5.1 DocType Oluşturma Pattern
```
Dizin: apps/{APP}/{APP}/{MODULE}/doctype/{snake_case_name}/
Dosyalar:
  {name}.json        → DocType schema
  {name}.py          → Python controller (class Name(Document))
  __init__.py         → Boş init
  {name}.js           → Client-side form script (opsiyonel)
  test_{name}.py      → Test (opsiyonel)
```

### 5.2 Child Table
- JSON'da "istable": 1
- Parent DocType'ta: "fieldtype": "Table", "options": "Child DocType Name"
- parent field otomatik oluşur — elle ekleme

### 5.3 hooks.py Altın Kuralları
1. ASLA hooks.py'yi silme/üzerine yazma — mevcut dict'lere SADECE ekle
2. doc_events["*"] wildcard tradehub_core'da — TÜM DocType'ları etkiler
3. Yeni doc_events eklerken wildcard'ı geçersiz kılma
4. scheduler_events → hourly, daily, weekly, monthly, cron
5. app_include_js → Global JS, her sayfa yüklemesinde çalışır

### 5.4 Python API
```python
doc = frappe.get_doc("DocType", name)
doc = frappe.new_doc("DocType")
frappe.get_list("DocType", filters={...}, fields=[...])
frappe.db.get_value("DocType", name, "field")
frappe.db.set_value("DocType", name, "field", value)
@frappe.whitelist()
def fn(param): ...
frappe.throw(_("Hata"))    # i18n zorunlu
frappe.enqueue("path.fn")  # async
```

### 5.5 Client-Side JS
```javascript
frappe.ui.form.on('DocType', {
    setup(frm) {},
    refresh(frm) {},
    field_name(frm) {}
});
frm.set_query('field', () => ({ filters: { key: val } }));
```

### 5.6 Migration Patch
```python
# {app}/patches/patch_name.py
import frappe
def execute():
    # idempotent olmalı
    frappe.db.commit()
```
patches.txt'e kayıt: {app_name}.patches.patch_name

---

## 6. KESİNLİKLE YAPILMAMASI GEREKENLER

1. Raw SQL yazma → Frappe ORM kullan
2. DocType adlarını hardcode etme → grep/find ile tespit et
3. Deprecated v13/v14 API → v15 API kullan
4. hooks.py silme/üzerine yazma → sadece ekle
5. Field'ları DB'den doğrudan silme → patches.txt ile migration
6. String interpolation SQL → %(param)s kullan
7. i18n'siz mesaj → _("...") zorunlu
8. Senkron uzun işlem → frappe.enqueue() kullan
9. Mevcut validate() override → ek çağrı ekle
10. Mevcut test silme → sadece ekle
11. Yukarı yönde cross-app referans → bağımlılık zincirini takip et
12. Wildcard doc_events geçersiz kılma → core'daki "*" tüm DocType'ları etkiler

---

## 7. BENCH KOMUTLARI

```bash
bench --site dev.localhost migrate
bench build --app {app_name}
bench --site dev.localhost run-tests --app {app_name}
bench --site dev.localhost clear-cache
bench --site dev.localhost console
bench --site dev.localhost run-tests --doctype "DocType Name"
```

---

## 8. AUTO-CLAUDE TASK YAZIM KURALLARI

### Atomik task kuralı
- 1-3 dosya → --complexity simple
- 4-8 dosya → --complexity standard
- 9+ dosya → BÖLÜN

### QA kriterleri
✅ dosya mevcut, grep pattern, py_compile hatasız, bench migrate
❌ "formda dropdown çalışıyor" (site gerektirir)

### Hedef app belirtin
```
HEDEF APP: tradehub_seller
DocType dizini: apps/tradehub_seller/tradehub_seller/tradehub_seller/doctype/
hooks.py: apps/tradehub_seller/tradehub_seller/hooks.py
patches.txt: apps/tradehub_seller/tradehub_seller/patches.txt
```
