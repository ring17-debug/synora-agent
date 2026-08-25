use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use rand_core::OsRng;

use crate::hash::hash;
use crate::state::Address;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CryptoError {
    InvalidPublicKey,
    InvalidSignature,
    AddressMismatch,
}

#[derive(Debug, Clone)]
pub struct Keypair {
    signing_key: SigningKey,
}

impl Keypair {
    pub fn generate() -> Self {
        let mut rng = OsRng;
        let signing_key = SigningKey::generate(&mut rng);

        Self { signing_key }
    }

    pub fn from_bytes(bytes: &[u8; 32]) -> Self {
        Self {
            signing_key: SigningKey::from_bytes(bytes),
        }
    }

    pub fn secret_key_bytes(&self) -> [u8; 32] {
        self.signing_key.to_bytes()
    }

    pub fn public_key_bytes(&self) -> [u8; 32] {
        self.signing_key.verifying_key().to_bytes()
    }

    pub fn address(&self) -> Address {
        address_from_public_key(&self.public_key_bytes())
    }

    pub fn sign(&self, message: &[u8]) -> [u8; 64] {
        self.signing_key.sign(message).to_bytes()
    }
}

pub fn address_from_public_key(public_key: &[u8; 32]) -> Address {
    let digest = hash(public_key);

    let mut address = [0u8; 20];
    address.copy_from_slice(&digest[..20]);

    address
}

pub fn verify_signature(
    public_key: &[u8; 32],
    signature: &[u8; 64],
    message: &[u8],
) -> Result<(), CryptoError> {
    let verifying_key =
        VerifyingKey::from_bytes(public_key).map_err(|_| CryptoError::InvalidPublicKey)?;

    let signature = Signature::from_bytes(signature);

    verifying_key
        .verify(message, &signature)
        .map_err(|_| CryptoError::InvalidSignature)
}

pub fn verify_address(
    public_key: &[u8; 32],
    expected_address: &Address,
) -> Result<(), CryptoError> {
    let derived_address = address_from_public_key(public_key);

    if derived_address != *expected_address {
        return Err(CryptoError::AddressMismatch);
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_keypair_has_deterministic_address() {
        let keypair = Keypair::generate();

        assert_eq!(keypair.address(), keypair.address());
    }

    #[test]
    fn public_key_derives_same_address() {
        let keypair = Keypair::generate();

        let address = address_from_public_key(&keypair.public_key_bytes());

        assert_eq!(address, keypair.address());
    }

    #[test]
    fn signature_can_be_verified() {
        let keypair = Keypair::generate();

        let message = b"synora transaction";

        let signature = keypair.sign(message);

        verify_signature(&keypair.public_key_bytes(), &signature, message)
            .expect("signature should be valid");
    }

    #[test]
    fn modified_message_is_rejected() {
        let keypair = Keypair::generate();

        let signature = keypair.sign(b"original message");

        let result = verify_signature(&keypair.public_key_bytes(), &signature, b"modified message");

        assert_eq!(result, Err(CryptoError::InvalidSignature));
    }

    #[test]
    fn wrong_public_key_is_rejected() {
        let keypair = Keypair::generate();
        let other_keypair = Keypair::generate();

        let message = b"synora transaction";

        let signature = keypair.sign(message);

        let result = verify_signature(&other_keypair.public_key_bytes(), &signature, message);

        assert_eq!(result, Err(CryptoError::InvalidSignature));
    }

    #[test]
    fn address_matches_public_key() {
        let keypair = Keypair::generate();

        verify_address(&keypair.public_key_bytes(), &keypair.address())
            .expect("address should match public key");
    }

    #[test]
    fn wrong_address_is_rejected() {
        let keypair = Keypair::generate();

        let wrong_address = [0xFF; 20];

        assert_eq!(
            verify_address(&keypair.public_key_bytes(), &wrong_address,),
            Err(CryptoError::AddressMismatch)
        );
    }

    #[test]
    fn known_key_is_deterministic() {
        let secret = [7u8; 32];

        let keypair = Keypair::from_bytes(&secret);

        assert_eq!(keypair.secret_key_bytes(), secret);

        assert_eq!(
            keypair.address(),
            address_from_public_key(&keypair.public_key_bytes())
        );
    }
}
