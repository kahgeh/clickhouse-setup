fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:rerun-if-changed=proto/backend.proto");
    tonic_prost_build::configure().compile_protos(&["proto/backend.proto"], &["proto"])?;
    Ok(())
}
