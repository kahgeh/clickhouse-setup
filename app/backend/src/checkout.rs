//! Fictitious checkout work done by `backend.Checkout`, one span per function.
//!
//! Every impl block is annotated with `#[trace_all]`, so each function below is a span. The call
//! tree is fixed (the sleeps only give the waterfall visible widths), which makes the number of
//! spans per request a constant (10 here plus the caller's server span):
//!
//! ```text
//! lookup_stock
//! └─ read_shelf
//! check_fraud
//! ├─ load_history
//! └─ score
//! authorise_payment
//! └─ call_acquirer
//! write_ledger
//! ├─ debit
//! └─ credit
//! ```
//!
//! The caller owns the parent span (`backend.Checkout`, server kind), which is not counted here.

use std::time::Duration;

use trace_macros::trace_all;

async fn pause(ms: u64) {
    tokio::time::sleep(Duration::from_millis(ms)).await;
}

struct InventoryStore;

#[trace_all]
impl InventoryStore {
    async fn lookup_stock(&self) -> u32 {
        pause(2).await;
        self.read_shelf().await
    }

    async fn read_shelf(&self) -> u32 {
        pause(3).await;
        42
    }
}

struct FraudChecker;

#[trace_all]
impl FraudChecker {
    async fn check_fraud(&self) -> bool {
        self.load_history().await;
        self.score().await < 50
    }

    async fn load_history(&self) {
        pause(2).await;
    }

    async fn score(&self) -> u32 {
        pause(1).await;
        7
    }
}

struct PaymentGateway;

#[trace_all]
impl PaymentGateway {
    async fn authorise_payment(&self) -> String {
        pause(1).await;
        self.call_acquirer().await
    }

    async fn call_acquirer(&self) -> String {
        pause(5).await;
        "auth-0001".to_string()
    }
}

struct Ledger;

#[trace_all]
impl Ledger {
    async fn write_ledger(&self) {
        self.debit().await;
        self.credit().await;
    }

    async fn debit(&self) {
        pause(2).await;
    }

    async fn credit(&self) {
        pause(2).await;
    }
}

/// Runs the whole checkout and returns the order id.
pub async fn process(cart_id: &str) -> Result<String, &'static str> {
    if InventoryStore.lookup_stock().await == 0 {
        return Err("out of stock");
    }
    if !FraudChecker.check_fraud().await {
        return Err("fraud suspected");
    }
    let auth = PaymentGateway.authorise_payment().await;
    Ledger.write_ledger().await;
    Ok(format!("order-{cart_id}-{auth}"))
}
