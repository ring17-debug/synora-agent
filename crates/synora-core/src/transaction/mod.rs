use crate::crypto::{CryptoError, Keypair, verify_address, verify_signature};
use crate::hash::{Hash, hash};

pub type Address = [u8; 20];

/// Maximum transaction calldata size.
///
/// Keeping this bounded prevents an attacker from submitting arbitrarily
/// large transactions and consuming excessive memory in the mempool/block.
pub const MAX_TRANSACTION_DATA_SIZE: usize = 128 * 1024;

pub const PUBLIC_KEY_SIZE: usize = 32;
pub const SIGNATURE_SIZE: usize = 64;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Transaction {
    pub chain_id: u64,
    pub nonce: u64,
    pub sender: Address,
    pub recipient: Address,
    pub value: u64,
    pub gas_limit: u64,
    pub gas_price: u64,
    pub data: Vec<u8>,
    pub public_key: [u8; PUBLIC_KEY_SIZE],
    pub signature: [u8; SIGNATURE_SIZE],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransactionValidationError {
    ZeroGasLimit,
    DataTooLarge,
    FeeOverflow,
    CostOverflow,
}

impl Transaction {
    #[expect(clippy::too_many_arguments)]
    pub fn new(
        chain_id: u64,
        nonce: u64,
        sender: Address,
        recipient: Address,
        value: u64,
        gas_limit: u64,
        gas_price: u64,
        data: Vec<u8>,
    ) -> Self {
        Self {
            chain_id,
            nonce,
            sender,
            recipient,
            value,
            gas_limit,
            gas_price,
            data,
            public_key: [0u8; PUBLIC_KEY_SIZE],
            signature: [0u8; SIGNATURE_SIZE],
        }
    }

    /// Validate transaction-level invariants.
    ///
    /// State-dependent checks such as nonce and balance belong to the
    /// mempool/executor layer.
    pub fn validate(&self) -> Result<(), TransactionValidationError> {
        if self.gas_limit == 0 {
            return Err(TransactionValidationError::ZeroGasLimit);
        }

        if self.data.len() > MAX_TRANSACTION_DATA_SIZE {
            return Err(TransactionValidationError::DataTooLarge);
        }

        self.checked_total_fee()
            .ok_or(TransactionValidationError::FeeOverflow)?;

        self.checked_total_cost()
            .ok_or(TransactionValidationError::CostOverflow)?;

        Ok(())
    }

    /// Calculate the fee without silently saturating on overflow.
    pub fn checked_total_fee(&self) -> Option<u64> {
        self.gas_limit.checked_mul(self.gas_price)
    }

    /// Calculate value + fee without silently saturating on overflow.
    pub fn checked_total_cost(&self) -> Option<u64> {
        self.value.checked_add(self.checked_total_fee()?)
    }

    /// Compatibility helper.
    ///
    /// Valid transactions always have a representable fee. Invalid
    /// transactions are rejected by `validate()` before entering the
    /// mempool/executor.
    pub fn total_fee(&self) -> u64 {
        self.checked_total_fee().unwrap_or(u64::MAX)
    }

    /// Compatibility helper.
    pub fn total_cost(&self) -> u64 {
        self.checked_total_cost().unwrap_or(u64::MAX)
    }

    pub fn is_contract_call(&self) -> bool {
        !self.data.is_empty()
    }

    /// Returns true when the transaction contains a public key and signature.
    pub fn is_signed(&self) -> bool {
        self.public_key != [0u8; PUBLIC_KEY_SIZE] && self.signature != [0u8; SIGNATURE_SIZE]
    }

    /// Calculate the hash that is signed by the sender.
    ///
    /// The public key and signature are intentionally excluded from this
    /// hash. This keeps the transaction hash stable before and after signing.
    pub fn signing_hash(&self) -> Hash {
        self.hash()
    }

    /// Sign the transaction with the supplied keypair.
    ///
    /// The keypair must correspond to the transaction sender.
    pub fn sign(&mut self, keypair: &Keypair) -> Result<(), CryptoError> {
        let public_key = keypair.public_key_bytes();

        verify_address(&public_key, &self.sender)?;

        let signing_hash = self.signing_hash();
        let signature = keypair.sign(&signing_hash);

        self.public_key = public_key;
        self.signature = signature;

        Ok(())
    }

    /// Verify that the transaction signature belongs to its sender.
    pub fn verify_signature(&self) -> Result<(), CryptoError> {
        if !self.is_signed() {
            return Err(CryptoError::InvalidSignature);
        }

        verify_address(&self.public_key, &self.sender)?;

        let signing_hash = self.signing_hash();

        verify_signature(&self.public_key, &self.signature, &signing_hash)
    }

    /// Return the canonical transaction hash.
    ///
    /// Signature and public key are excluded intentionally. The transaction
    /// identity is determined by the signed transaction payload itself.
    pub fn hash(&self) -> Hash {
        let mut bytes = Vec::with_capacity(8 + 8 + 20 + 20 + 8 + 8 + 8 + 8 + self.data.len());

        bytes.extend_from_slice(&self.chain_id.to_le_bytes());
        bytes.extend_from_slice(&self.nonce.to_le_bytes());
        bytes.extend_from_slice(&self.sender);
        bytes.extend_from_slice(&self.recipient);
        bytes.extend_from_slice(&self.value.to_le_bytes());
        bytes.extend_from_slice(&self.gas_limit.to_le_bytes());
        bytes.extend_from_slice(&self.gas_price.to_le_bytes());

        let data_len = self.data.len() as u64;
        bytes.extend_from_slice(&data_len.to_le_bytes());
        bytes.extend_from_slice(&self.data);

        hash(&bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_transaction() -> Transaction {
        Transaction::new(1, 0, [1u8; 20], [2u8; 20], 10_000, 21_000, 1, Vec::new())
    }

    #[test]
    fn transaction_cost_is_calculated() {
        let tx = test_transaction();

        assert_eq!(tx.total_fee(), 21_000);
        assert_eq!(tx.total_cost(), 31_000);
        assert!(!tx.is_contract_call());
        assert!(!tx.is_signed());
    }

    #[test]
    fn transaction_hash_is_deterministic() {
        let tx = test_transaction();

        assert_eq!(tx.hash(), tx.hash());
        assert_eq!(tx.signing_hash(), tx.hash());
    }

    #[test]
    fn different_transactions_have_different_hashes() {
        let tx1 = test_transaction();

        let tx2 = Transaction::new(1, 1, [1u8; 20], [2u8; 20], 10_000, 21_000, 1, Vec::new());

        assert_ne!(tx1.hash(), tx2.hash());
    }

    #[test]
    fn transaction_data_changes_hash() {
        let tx1 = test_transaction();

        let tx2 = Transaction::new(1, 0, [1u8; 20], [2u8; 20], 10_000, 21_000, 1, vec![1, 2, 3]);

        assert_ne!(tx1.hash(), tx2.hash());
    }

    #[test]
    fn transaction_validation_succeeds() {
        assert_eq!(test_transaction().validate(), Ok(()));
    }

    #[test]
    fn zero_gas_limit_is_rejected() {
        let tx = Transaction::new(1, 0, [1u8; 20], [2u8; 20], 10, 0, 1, Vec::new());

        assert_eq!(tx.validate(), Err(TransactionValidationError::ZeroGasLimit));
    }

    #[test]
    fn fee_overflow_is_detected() {
        let tx = Transaction::new(1, 0, [1u8; 20], [2u8; 20], 0, u64::MAX, 2, Vec::new());

        assert_eq!(tx.validate(), Err(TransactionValidationError::FeeOverflow));
        assert_eq!(tx.checked_total_fee(), None);
    }

    #[test]
    fn cost_overflow_is_detected() {
        let tx = Transaction::new(1, 0, [1u8; 20], [2u8; 20], u64::MAX, 1, 1, Vec::new());

        assert_eq!(tx.validate(), Err(TransactionValidationError::CostOverflow));
    }

    #[test]
    fn oversized_data_is_rejected() {
        let tx = Transaction::new(
            1,
            0,
            [1u8; 20],
            [2u8; 20],
            0,
            21_000,
            1,
            vec![0u8; MAX_TRANSACTION_DATA_SIZE + 1],
        );

        assert_eq!(tx.validate(), Err(TransactionValidationError::DataTooLarge));
    }
}

#[cfg(test)]
mod signing_tests {
    use super::*;
    use crate::crypto::Keypair;

    fn unsigned_transaction(sender: Address) -> Transaction {
        Transaction::new(1337, 0, sender, [2u8; 20], 10_000, 21_000, 1, Vec::new())
    }

    #[test]
    fn transaction_can_be_signed_and_verified() {
        let keypair = Keypair::generate();

        let mut tx = unsigned_transaction(keypair.address());

        assert!(!tx.is_signed());

        tx.sign(&keypair).expect("transaction should be signed");

        assert!(tx.is_signed());

        tx.verify_signature().expect("signature should be valid");
    }

    #[test]
    fn modified_transaction_is_rejected() {
        let keypair = Keypair::generate();

        let mut tx = unsigned_transaction(keypair.address());

        tx.sign(&keypair).expect("transaction should be signed");

        tx.value += 1;

        assert_eq!(tx.verify_signature(), Err(CryptoError::InvalidSignature));
    }

    #[test]
    fn wrong_keypair_cannot_sign_for_sender() {
        let sender_keypair = Keypair::generate();
        let other_keypair = Keypair::generate();

        let mut tx = unsigned_transaction(sender_keypair.address());

        assert_eq!(tx.sign(&other_keypair), Err(CryptoError::AddressMismatch));
    }

    #[test]
    fn unsigned_transaction_is_rejected() {
        let keypair = Keypair::generate();

        let tx = unsigned_transaction(keypair.address());

        assert!(!tx.is_signed());

        assert_eq!(tx.verify_signature(), Err(CryptoError::InvalidSignature));
    }

    #[test]
    fn signing_does_not_change_transaction_hash() {
        let keypair = Keypair::generate();

        let mut tx = unsigned_transaction(keypair.address());

        let hash_before = tx.hash();

        tx.sign(&keypair).expect("transaction should be signed");

        let hash_after = tx.hash();

        assert_eq!(hash_before, hash_after);
        assert_eq!(tx.signing_hash(), hash_before);
    }
}
