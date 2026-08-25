use std::collections::HashMap;

use crate::crypto::CryptoError;
use crate::hash::Hash;
use crate::state::State;
use crate::transaction::Transaction;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MempoolError {
    MempoolFull,
    DuplicateTransaction,
    InvalidChainId,
    SenderNotFound,
    InvalidNonce,
    InsufficientBalance,
    InvalidTransaction,
    SenderNonceConflict,
    InvalidSignature,
}

#[derive(Debug)]
pub struct Mempool {
    chain_id: u64,
    capacity: usize,
    transactions: HashMap<Hash, Transaction>,
}

impl Mempool {
    pub fn new(chain_id: u64, capacity: usize) -> Self {
        Self {
            chain_id,
            capacity,
            transactions: HashMap::new(),
        }
    }

    pub fn chain_id(&self) -> u64 {
        self.chain_id
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn len(&self) -> usize {
        self.transactions.len()
    }

    pub fn is_empty(&self) -> bool {
        self.transactions.is_empty()
    }

    pub fn is_full(&self) -> bool {
        self.len() >= self.capacity
    }

    pub fn contains(&self, hash: &Hash) -> bool {
        self.transactions.contains_key(hash)
    }

    pub fn get(&self, hash: &Hash) -> Option<&Transaction> {
        self.transactions.get(hash)
    }

    pub fn submit(&mut self, state: &State, tx: Transaction) -> Result<Hash, MempoolError> {
        // ------------------------------------------------------------
        // 1. Chain ID harus sesuai dengan chain yang digunakan.
        // ------------------------------------------------------------
        if tx.chain_id != self.chain_id {
            return Err(MempoolError::InvalidChainId);
        }

        // ------------------------------------------------------------
        // 2. Validasi transaksi dasar.
        // ------------------------------------------------------------
        tx.validate()
            .map_err(|_| MempoolError::InvalidTransaction)?;

        // ------------------------------------------------------------
        // 3. Verifikasi signature.
        //
        // Signature harus:
        // - valid secara Ed25519
        // - cocok dengan public key
        // - public key menghasilkan sender address
        // - signature dibuat dari hash transaksi
        // ------------------------------------------------------------
        tx.verify_signature()
            .map_err(|_: CryptoError| MempoolError::InvalidSignature)?;

        // ------------------------------------------------------------
        // 4. Hitung hash setelah validasi dasar.
        // ------------------------------------------------------------
        let tx_hash = tx.hash();

        // ------------------------------------------------------------
        // 5. Tolak transaksi duplikat.
        // ------------------------------------------------------------
        if self.contains(&tx_hash) {
            return Err(MempoolError::DuplicateTransaction);
        }

        // ------------------------------------------------------------
        // 6. Jangan menerima transaksi jika mempool penuh.
        // ------------------------------------------------------------
        if self.is_full() {
            return Err(MempoolError::MempoolFull);
        }

        // ------------------------------------------------------------
        // 7. Sender harus sudah memiliki account.
        // ------------------------------------------------------------
        let sender = state
            .get_account(&tx.sender)
            .ok_or(MempoolError::SenderNotFound)?;

        // ------------------------------------------------------------
        // 8. Nonce harus sama persis dengan nonce account.
        // ------------------------------------------------------------
        if tx.nonce != sender.nonce {
            return Err(MempoolError::InvalidNonce);
        }

        // ------------------------------------------------------------
        // 9. Cegah dua transaksi dengan sender + nonce yang sama.
        // ------------------------------------------------------------
        if self
            .transactions
            .values()
            .any(|pending| pending.sender == tx.sender && pending.nonce == tx.nonce)
        {
            return Err(MempoolError::SenderNonceConflict);
        }

        // ------------------------------------------------------------
        // 10. Pastikan saldo sender cukup untuk value + fee.
        // ------------------------------------------------------------
        let total_cost = tx
            .checked_total_cost()
            .ok_or(MempoolError::InvalidTransaction)?;

        if sender.balance < u128::from(total_cost) {
            return Err(MempoolError::InsufficientBalance);
        }

        // ------------------------------------------------------------
        // 11. Masukkan transaksi ke mempool.
        // ------------------------------------------------------------
        self.transactions.insert(tx_hash, tx);

        Ok(tx_hash)
    }

    pub fn remove(&mut self, hash: &Hash) -> Option<Transaction> {
        self.transactions.remove(hash)
    }

    pub fn clear(&mut self) {
        self.transactions.clear();
    }

    pub fn transactions(&self) -> Vec<&Transaction> {
        let mut transactions: Vec<&Transaction> = self.transactions.values().collect();

        transactions.sort_by(|a, b| {
            b.gas_price
                .cmp(&a.gas_price)
                .then_with(|| a.nonce.cmp(&b.nonce))
                .then_with(|| a.hash().cmp(&b.hash()))
        });

        transactions
    }

    pub fn take(&mut self, max: usize) -> Vec<Transaction> {
        if max == 0 || self.transactions.is_empty() {
            return Vec::new();
        }

        let hashes: Vec<Hash> = self
            .transactions()
            .into_iter()
            .take(max)
            .map(Transaction::hash)
            .collect();

        hashes
            .into_iter()
            .filter_map(|hash| self.transactions.remove(&hash))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crypto::Keypair;

    fn signed_transaction(
        keypair: &Keypair,
        nonce: u64,
        recipient: [u8; 20],
        value: u64,
        gas_price: u64,
    ) -> Transaction {
        let sender = keypair.address();

        let mut tx = Transaction::new(
            1,
            nonce,
            sender,
            recipient,
            value,
            21_000,
            gas_price,
            Vec::new(),
        );

        tx.sign(keypair).expect("transaction should be signable");

        tx
    }

    fn state_with_accounts() -> (State, Keypair, Keypair) {
        let mut state = State::new();

        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);
        let bob_keypair = Keypair::from_bytes(&[2u8; 32]);

        state.create_account(alice_keypair.address(), 1_000_000);
        state.create_account(bob_keypair.address(), 0);

        (state, alice_keypair, bob_keypair)
    }

    #[test]
    fn mempool_starts_empty() {
        let mempool = Mempool::new(1, 100);

        assert_eq!(mempool.chain_id(), 1);
        assert_eq!(mempool.capacity(), 100);
        assert_eq!(mempool.len(), 0);
        assert!(mempool.is_empty());
        assert!(!mempool.is_full());
    }

    #[test]
    fn transaction_can_be_submitted() {
        let (state, alice, bob) = state_with_accounts();

        let tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);
        let tx_hash = tx.hash();

        let mut mempool = Mempool::new(1, 100);

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Ok(tx_hash));
        assert_eq!(mempool.len(), 1);
        assert!(mempool.contains(&tx_hash));
        assert!(mempool.get(&tx_hash).is_some());
    }

    #[test]
    fn duplicate_transaction_is_rejected() {
        let (state, alice, bob) = state_with_accounts();

        let tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        let mut mempool = Mempool::new(1, 100);

        mempool
            .submit(&state, tx.clone())
            .expect("first submission should work");

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Err(MempoolError::DuplicateTransaction));
        assert_eq!(mempool.len(), 1);
    }

    #[test]
    fn wrong_chain_id_is_rejected() {
        let (state, alice, bob) = state_with_accounts();

        let mut tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        tx.chain_id = 2;

        let mut mempool = Mempool::new(1, 100);

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Err(MempoolError::InvalidChainId));
    }

    #[test]
    fn missing_sender_is_rejected() {
        let mut state = State::new();

        let alice = Keypair::from_bytes(&[1u8; 32]);
        let bob = Keypair::from_bytes(&[2u8; 32]);

        state.create_account(bob.address(), 0);

        let tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        let mut mempool = Mempool::new(1, 100);

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Err(MempoolError::SenderNotFound));
    }

    #[test]
    fn wrong_nonce_is_rejected() {
        let (state, alice, bob) = state_with_accounts();

        let tx = signed_transaction(&alice, 1, bob.address(), 10_000, 1);

        let mut mempool = Mempool::new(1, 100);

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Err(MempoolError::InvalidNonce));
        assert_eq!(mempool.len(), 0);
    }

    #[test]
    fn insufficient_balance_is_rejected() {
        let mut state = State::new();

        let alice = Keypair::from_bytes(&[1u8; 32]);
        let bob = Keypair::from_bytes(&[2u8; 32]);

        state.create_account(alice.address(), 100);
        state.create_account(bob.address(), 0);

        let tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        let mut mempool = Mempool::new(1, 100);

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Err(MempoolError::InsufficientBalance));
    }

    #[test]
    fn capacity_is_enforced() {
        let (state, alice, bob) = state_with_accounts();

        let mut mempool = Mempool::new(1, 1);

        let tx1 = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        mempool
            .submit(&state, tx1)
            .expect("first transaction should work");

        let tx2 = signed_transaction(&bob, 0, alice.address(), 1, 1);

        // Bob has zero balance, but the mempool is already full.
        let result = mempool.submit(&state, tx2);

        assert_eq!(result, Err(MempoolError::MempoolFull));
    }

    #[test]
    fn transaction_can_be_removed() {
        let (state, alice, bob) = state_with_accounts();

        let tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);
        let tx_hash = tx.hash();

        let mut mempool = Mempool::new(1, 100);

        mempool.submit(&state, tx).expect("transaction should work");

        let removed = mempool.remove(&tx_hash);

        assert!(removed.is_some());
        assert_eq!(mempool.len(), 0);
        assert!(!mempool.contains(&tx_hash));
    }

    #[test]
    fn transactions_are_sorted_by_gas_price() {
        let (state, alice, bob) = state_with_accounts();

        let charlie = Keypair::from_bytes(&[3u8; 32]);

        let mut state2 = state.clone();
        state2.create_account(charlie.address(), 1_000_000);

        let tx1 = signed_transaction(&alice, 0, bob.address(), 10_000, 1);
        let tx2 = signed_transaction(&charlie, 0, bob.address(), 10_000, 10);

        let mut mempool = Mempool::new(1, 100);

        mempool.submit(&state, tx1).expect("tx1 should work");

        mempool.submit(&state2, tx2).expect("tx2 should work");

        let transactions = mempool.transactions();

        assert_eq!(transactions.len(), 2);
        assert_eq!(transactions[0].gas_price, 10);
        assert_eq!(transactions[1].gas_price, 1);
    }

    #[test]
    fn same_sender_and_nonce_conflict_is_rejected() {
        let (state, alice, bob) = state_with_accounts();

        let tx1 = signed_transaction(&alice, 0, bob.address(), 10_000, 1);
        let tx2 = signed_transaction(&alice, 0, bob.address(), 20_000, 10);

        let mut mempool = Mempool::new(1, 100);

        mempool
            .submit(&state, tx1)
            .expect("first transaction should work");

        let result = mempool.submit(&state, tx2);

        assert_eq!(result, Err(MempoolError::SenderNonceConflict));
        assert_eq!(mempool.len(), 1);
    }

    #[test]
    fn take_removes_highest_priority_transactions() {
        let (state, alice, bob) = state_with_accounts();

        let charlie = Keypair::from_bytes(&[3u8; 32]);

        let mut state2 = state.clone();
        state2.create_account(charlie.address(), 1_000_000);

        let tx1 = signed_transaction(&alice, 0, bob.address(), 10_000, 1);
        let tx2 = signed_transaction(&charlie, 0, bob.address(), 10_000, 10);

        let mut mempool = Mempool::new(1, 100);

        mempool.submit(&state, tx1).expect("tx1 should work");

        mempool
            .submit(&state2, tx2.clone())
            .expect("tx2 should work");

        let selected = mempool.take(1);

        assert_eq!(selected.len(), 1);
        assert_eq!(selected[0].hash(), tx2.hash());
        assert_eq!(mempool.len(), 1);
    }

    #[test]
    fn take_zero_does_not_remove_transactions() {
        let (state, alice, bob) = state_with_accounts();

        let tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        let mut mempool = Mempool::new(1, 100);

        mempool.submit(&state, tx).expect("transaction should work");

        let selected = mempool.take(0);

        assert!(selected.is_empty());
        assert_eq!(mempool.len(), 1);
    }

    #[test]
    fn clear_removes_all_transactions() {
        let (state, alice, bob) = state_with_accounts();

        let tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        let mut mempool = Mempool::new(1, 100);

        mempool.submit(&state, tx).expect("transaction should work");

        assert_eq!(mempool.len(), 1);

        mempool.clear();

        assert_eq!(mempool.len(), 0);
        assert!(mempool.is_empty());
    }

    #[test]
    fn invalid_signature_is_rejected() {
        let (state, alice, bob) = state_with_accounts();

        let mut tx = signed_transaction(&alice, 0, bob.address(), 10_000, 1);

        tx.signature[0] ^= 0xFF;

        let mut mempool = Mempool::new(1, 100);

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Err(MempoolError::InvalidSignature));
        assert_eq!(mempool.len(), 0);
    }

    #[test]
    fn unsigned_transaction_is_rejected() {
        let (state, alice, bob) = state_with_accounts();

        let tx = Transaction::new(
            1,
            0,
            alice.address(),
            bob.address(),
            10_000,
            21_000,
            1,
            Vec::new(),
        );

        let mut mempool = Mempool::new(1, 100);

        let result = mempool.submit(&state, tx);

        assert_eq!(result, Err(MempoolError::InvalidSignature));
        assert_eq!(mempool.len(), 0);
    }
}
