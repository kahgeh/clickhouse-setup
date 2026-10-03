//! Expansion tests: the macro is used on real code and the spans it produces are recorded
//! with a small tracing layer.

use std::sync::{Arc, Mutex};

use trace_macros::trace_all;
use tracing::Subscriber;
use tracing::span::Attributes;
use tracing_subscriber::layer::{Context, SubscriberExt};
use tracing_subscriber::registry::LookupSpan;
use tracing_subscriber::{Layer, Registry};

#[derive(Clone, Default)]
struct Recorder(Arc<Mutex<Vec<String>>>);

impl<S: Subscriber + for<'a> LookupSpan<'a>> Layer<S> for Recorder {
    fn on_new_span(&self, attrs: &Attributes<'_>, _id: &tracing::span::Id, _ctx: Context<'_, S>) {
        self.0
            .lock()
            .unwrap()
            .push(attrs.metadata().name().to_string());
    }
}

impl Recorder {
    fn names(&self) -> Vec<String> {
        self.0.lock().unwrap().clone()
    }
}

fn record() -> (Recorder, tracing::subscriber::DefaultGuard) {
    let rec = Recorder::default();
    let guard = tracing::subscriber::set_default(Registry::default().with(rec.clone()));
    (rec, guard)
}

/// Deliberately has no `Debug`: only compiles if the span records no arguments.
struct Opaque;

struct Svc;

#[trace_all]
impl Svc {
    fn first(&self) {
        self.second(Opaque);
    }

    fn second(&self, _arg: Opaque) {}

    async fn third(&self, _arg: Opaque) {}
}

trait Greeter {
    fn greet(&self, _who: Opaque);
}

#[trace_all]
impl Greeter for Svc {
    fn greet(&self, _who: Opaque) {}
}

#[test]
fn impl_block_fns_get_spans() {
    let (rec, _g) = record();
    Svc.first();
    assert_eq!(rec.names(), ["first", "second"]);
}

#[test]
fn trait_impl_fns_get_spans() {
    let (rec, _g) = record();
    Svc.greet(Opaque);
    assert_eq!(rec.names(), ["greet"]);
}

#[tokio::test]
async fn async_fn_keeps_working() {
    let (rec, _g) = record();
    Svc.third(Opaque).await;
    assert_eq!(rec.names(), ["third"]);
}

#[trace_all]
mod inline {
    use super::Opaque;

    pub fn outer() {
        inner(Opaque);
        fn nested_in_body() {}
        nested_in_body();
    }

    fn inner(_arg: Opaque) {}

    pub mod deeper {
        pub fn deep() {}
    }

    pub trait Defaults {
        fn declared(&self);
        fn provided(&self) {}
    }

    impl Defaults for () {
        fn declared(&self) {}
    }
}

#[test]
fn inline_mod_fns_get_spans_recursively() {
    use inline::Defaults;
    let (rec, _g) = record();
    inline::outer();
    inline::deeper::deep();
    ().declared();
    ().provided();
    assert_eq!(
        rec.names(),
        [
            "outer",
            "inner",
            "nested_in_body",
            "deep",
            "declared",
            "provided"
        ]
    );
}

struct Skipping;

#[trace_all(skip(quiet, also_quiet))]
impl Skipping {
    fn loud(&self) {
        self.quiet();
        self.also_quiet();
    }

    fn quiet(&self) {}

    fn also_quiet(&self) {}
}

#[test]
fn skip_list_leaves_named_fns_alone() {
    let (rec, _g) = record();
    Skipping.loud();
    assert_eq!(rec.names(), ["loud"]);
}

struct Manual;

#[trace_all]
impl Manual {
    #[tracing::instrument(skip_all, name = "custom")]
    fn qualified(&self) {}

    #[tracing::instrument(skip_all)]
    fn already_instrumented(&self) {}
}

#[trace_all]
mod manual_mod {
    use tracing::instrument;

    #[instrument(skip_all, name = "renamed")]
    pub fn imported() {}
}

#[test]
fn existing_instrument_is_untouched() {
    let (rec, _g) = record();
    Manual.qualified();
    Manual.already_instrumented();
    manual_mod::imported();
    assert_eq!(rec.names(), ["custom", "already_instrumented", "renamed"]);
}

struct WithConst;

#[trace_all]
impl WithConst {
    const fn constant(&self) -> u32 {
        7
    }

    fn traced(&self) -> u32 {
        self.constant()
    }
}

#[test]
fn const_fn_is_left_alone_and_the_block_still_compiles() {
    let (rec, _guard) = record();
    assert_eq!(WithConst.traced(), 7);
    assert_eq!(rec.names(), ["traced"]);
}
