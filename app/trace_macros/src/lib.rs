//! `#[trace_all]`: one attribute that puts a span on every function of an `impl` block or an
//! inline `mod`, instead of repeating `#[tracing::instrument(skip_all)]` on each one.
//!
//! ```ignore
//! #[trace_all]                 // all fns
//! #[trace_all(skip(helper))]   // all fns except those named `helper`
//! ```
//!
//! Every fn without an `#[instrument]` / `#[tracing::instrument]` of its own gets
//! `#[tracing::instrument(skip_all)]`: the span is named after the fn, no arguments are
//! recorded (so they need not implement `Debug`) and `async fn` keeps working. Fns found inside
//! the annotated item are covered too: methods of nested impls, fns of nested inline modules
//! and fn items declared inside fn bodies. Default methods of traits declared inside a
//! module are covered, bodiless trait method declarations are not. A `mod name;` that lives in
//! another file cannot be seen by an attribute macro, put the attribute inside that file.
//!
//! `const fn`s are left alone, because `#[instrument]` does not compile on them. A name in
//! `skip(..)` that matches no fn in the block is a compile error.
//!
//! The crate using the macro needs a direct dependency on `tracing`.

use proc_macro::TokenStream;
use syn::visit_mut::{self, VisitMut};
use syn::{
    Attribute, Ident, ImplItemFn, Item, ItemFn, Signature, TraitItemFn, parse_macro_input,
    parse_quote,
};

#[proc_macro_attribute]
pub fn trace_all(args: TokenStream, item: TokenStream) -> TokenStream {
    let mut skip = Vec::new();
    let parser = syn::meta::parser(|meta| {
        if meta.path.is_ident("skip") {
            meta.parse_nested_meta(|name| {
                skip.push(name.path.require_ident()?.clone());
                Ok(())
            })
        } else {
            Err(meta.error("unsupported option, expected `skip(name, ...)`"))
        }
    });
    parse_macro_input!(args with parser);

    let mut item = parse_macro_input!(item as Item);
    if !matches!(&item, Item::Impl(_) | Item::Mod(_)) {
        return syn::Error::new_spanned(
            &item,
            "#[trace_all] applies to `impl` blocks and `mod` blocks",
        )
        .to_compile_error()
        .into();
    }
    if let Item::Mod(m) = &item
        && m.content.is_none()
    {
        return syn::Error::new_spanned(
            &item,
            "#[trace_all] needs an inline `mod name { .. }`; put it inside the module file instead",
        )
        .to_compile_error()
        .into();
    }
    let mut instrumenter = Instrumenter {
        skip,
        seen: Vec::new(),
    };
    instrumenter.visit_item_mut(&mut item);
    // A misspelt name in `skip(..)` would silently instrument the fn it was meant to spare.
    let unknown = instrumenter
        .skip
        .iter()
        .filter(|name| !instrumenter.seen.contains(name))
        .map(|name| syn::Error::new(name.span(), format!("no fn named `{name}` in this block")))
        .reduce(|mut all, e| {
            all.combine(e);
            all
        });
    if let Some(err) = unknown {
        // Keep the item so the caller sees this error and not a second one about missing methods.
        let err = err.to_compile_error();
        return quote::quote!(#err #item).into();
    }
    quote::quote!(#item).into()
}

struct Instrumenter {
    skip: Vec<Ident>,
    /// Names of every fn visited, so `skip(..)` entries that match nothing can be reported.
    seen: Vec<Ident>,
}

impl Instrumenter {
    fn instrument(&mut self, sig: &Signature, attrs: &mut Vec<Attribute>) {
        let name = &sig.ident;
        self.seen.push(name.clone());
        // `#[instrument]` does not compile on a `const fn`, which cannot create a span anyway.
        if self.skip.contains(name) || sig.constness.is_some() || attrs.iter().any(is_instrument) {
            return;
        }
        attrs.push(parse_quote!(#[tracing::instrument(skip_all)]));
    }
}

/// `#[instrument]`, `#[tracing::instrument]` or any other path ending in `instrument`.
fn is_instrument(attr: &Attribute) -> bool {
    attr.path()
        .segments
        .last()
        .is_some_and(|s| s.ident == "instrument")
}

impl VisitMut for Instrumenter {
    fn visit_item_fn_mut(&mut self, f: &mut ItemFn) {
        self.instrument(&f.sig, &mut f.attrs);
        visit_mut::visit_item_fn_mut(self, f);
    }

    fn visit_impl_item_fn_mut(&mut self, f: &mut ImplItemFn) {
        self.instrument(&f.sig, &mut f.attrs);
        visit_mut::visit_impl_item_fn_mut(self, f);
    }

    fn visit_trait_item_fn_mut(&mut self, f: &mut TraitItemFn) {
        if f.default.is_some() {
            self.instrument(&f.sig, &mut f.attrs);
        }
        visit_mut::visit_trait_item_fn_mut(self, f);
    }
}
