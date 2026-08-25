use crate::hash::{Hash, hash, hash_pair, zero_hash};
use crate::state::State;
use crate::transaction::Transaction;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BlockHeader {
    pub version: u8,
    pub chain_id: u64,
    pub height: u64,
    pub timestamp: u64,
    pub previous_hash: Hash,
    pub state_root: Hash,
    pub transactions_root: Hash,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Block {
    pub header: BlockHeader,
    pub transactions: Vec<Transaction>,
}

impl BlockHeader {
    pub fn genesis(chain_id: u64, timestamp: u64) -> Self {
        Self {
            version: 1,
            chain_id,
            height: 0,
            timestamp,
            previous_hash: zero_hash(),
            state_root: zero_hash(),
            transactions_root: zero_hash(),
        }
    }

    pub fn hash(&self) -> Hash {
        let mut bytes = Vec::with_capacity(1 + 8 + 8 + 8 + 32 + 32 + 32);

        bytes.push(self.version);
        bytes.extend_from_slice(&self.chain_id.to_le_bytes());
        bytes.extend_from_slice(&self.height.to_le_bytes());
        bytes.extend_from_slice(&self.timestamp.to_le_bytes());
        bytes.extend_from_slice(&self.previous_hash);
        bytes.extend_from_slice(&self.state_root);
        bytes.extend_from_slice(&self.transactions_root);

        hash(&bytes)
    }
}

impl Block {
    pub fn genesis(chain_id: u64, timestamp: u64) -> Self {
        Self {
            header: BlockHeader::genesis(chain_id, timestamp),
            transactions: Vec::new(),
        }
    }

    pub fn new(
        chain_id: u64,
        height: u64,
        timestamp: u64,
        previous_hash: Hash,
        state_root: Hash,
        transactions_root: Hash,
        transactions: Vec<Transaction>,
    ) -> Self {
        Self {
            header: BlockHeader {
                version: 1,
                chain_id,
                height,
                timestamp,
                previous_hash,
                state_root,
                transactions_root,
            },
            transactions,
        }
    }

    pub fn from_state(
        chain_id: u64,
        height: u64,
        timestamp: u64,
        previous_hash: Hash,
        state: &State,
        transactions: Vec<Transaction>,
    ) -> Self {
        let state_root = state.state_root();
        let transactions_root = Self::calculate_transactions_root(&transactions);

        Self::new(
            chain_id,
            height,
            timestamp,
            previous_hash,
            state_root,
            transactions_root,
            transactions,
        )
    }

    pub fn transaction_count(&self) -> usize {
        self.transactions.len()
    }

    pub fn is_genesis(&self) -> bool {
        self.header.height == 0 && self.header.previous_hash == zero_hash()
    }

    pub fn transactions_root(&self) -> Hash {
        Self::calculate_transactions_root(&self.transactions)
    }

    fn calculate_transactions_root(transactions: &[Transaction]) -> Hash {
        if transactions.is_empty() {
            return zero_hash();
        }

        let mut hashes: Vec<Hash> = transactions.iter().map(Transaction::hash).collect();

        while hashes.len() > 1 {
            let mut next = Vec::with_capacity(hashes.len().div_ceil(2));

            for pair in hashes.chunks(2) {
                let left = pair[0];
                let right = if pair.len() == 2 { pair[1] } else { pair[0] };

                next.push(hash_pair(&left, &right));
            }

            hashes = next;
        }

        hashes[0]
    }

    pub fn hash(&self) -> Hash {
        self.header.hash()
    }

    pub fn validate_state_root(&self, state: &State) -> bool {
        self.header.state_root == state.state_root()
    }

    pub fn validate_transactions_root(&self) -> bool {
        self.header.transactions_root == self.transactions_root()
    }

    pub fn validate_roots(&self, state: &State) -> bool {
        self.validate_state_root(state) && self.validate_transactions_root()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_transaction(nonce: u64) -> Transaction {
        Transaction::new(
            1,
            nonce,
            [1u8; 20],
            [2u8; 20],
            10_000,
            21_000,
            1,
            Vec::new(),
        )
    }

    #[test]
    fn genesis_block_is_valid() {
        let block = Block::genesis(1, 1_700_000_000);

        assert!(block.is_genesis());
        assert_eq!(block.header.chain_id, 1);
        assert_eq!(block.header.height, 0);
        assert_eq!(block.transaction_count(), 0);
    }

    #[test]
    fn normal_block_contains_transactions() {
        let tx = test_transaction(0);

        let block = Block::new(
            1,
            1,
            1_700_000_100,
            [1u8; 32],
            [2u8; 32],
            [3u8; 32],
            vec![tx],
        );

        assert!(!block.is_genesis());
        assert_eq!(block.header.height, 1);
        assert_eq!(block.transaction_count(), 1);
    }

    #[test]
    fn block_hash_is_deterministic() {
        let block = Block::genesis(1, 1_700_000_000);

        assert_eq!(block.hash(), block.hash());
    }

    #[test]
    fn different_blocks_have_different_hashes() {
        let block1 = Block::genesis(1, 1_700_000_000);
        let block2 = Block::genesis(1, 1_700_000_001);

        assert_ne!(block1.hash(), block2.hash());
    }

    #[test]
    fn transactions_root_is_deterministic() {
        let tx1 = test_transaction(0);
        let tx2 = test_transaction(1);

        let block = Block::new(
            1,
            1,
            1_700_000_100,
            [0u8; 32],
            [0u8; 32],
            [0u8; 32],
            vec![tx1, tx2],
        );

        assert_eq!(block.transactions_root(), block.transactions_root());
    }

    #[test]
    fn from_state_uses_state_root() {
        let alice = [1u8; 20];

        let mut state = State::new();
        state.create_account(alice, 1_000);

        let block = Block::from_state(1, 1, 1_700_000_100, [0u8; 32], &state, Vec::new());

        assert_eq!(block.header.state_root, state.state_root());
        assert!(block.validate_state_root(&state));
    }

    #[test]
    fn from_state_uses_transactions_root() {
        let state = State::new();

        let transactions = vec![test_transaction(0), test_transaction(1)];

        let block = Block::from_state(1, 1, 1_700_000_100, [0u8; 32], &state, transactions);

        assert!(block.validate_transactions_root());
    }

    #[test]
    fn block_roots_are_valid() {
        let alice = [1u8; 20];

        let mut state = State::new();
        state.create_account(alice, 10_000);

        let tx = test_transaction(0);

        let block = Block::from_state(1, 1, 1_700_000_100, [0u8; 32], &state, vec![tx]);

        assert!(block.validate_roots(&state));
    }

    #[test]
    fn changed_state_invalidates_old_block_root() {
        let alice = [1u8; 20];

        let mut state = State::new();
        state.create_account(alice, 10_000);

        let block = Block::from_state(1, 1, 1_700_000_100, [0u8; 32], &state, Vec::new());

        state.burn(alice, 1_000).expect("burn should work");

        assert!(!block.validate_state_root(&state));
    }
}
