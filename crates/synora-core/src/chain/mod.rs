use crate::block::Block;
use crate::execution::{ExecutionError, ExecutionReceipt, Executor};
use crate::hash::{Hash, hash_pair, zero_hash};
use crate::state::{Address, State};
use crate::transaction::Transaction;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChainError {
    InvalidGenesis,
    InvalidChainId,
    InvalidHeight,
    InvalidPreviousHash,
    InvalidTimestamp,
    ExecutionFailed(ExecutionError),
}

pub struct Blockchain {
    chain_id: u64,
    executor: Executor,
    state: State,
    blocks: Vec<Block>,
}

impl Blockchain {
    pub fn new(chain_id: u64, timestamp: u64, state: State, fee_recipient: Address) -> Self {
        let executor = Executor::new(chain_id, fee_recipient);
        let genesis = Block::genesis(chain_id, timestamp);

        Self {
            chain_id,
            executor,
            state,
            blocks: vec![genesis],
        }
    }

    pub fn chain_id(&self) -> u64 {
        self.chain_id
    }

    pub fn height(&self) -> u64 {
        self.blocks.len().saturating_sub(1) as u64
    }

    pub fn block_count(&self) -> usize {
        self.blocks.len()
    }

    pub fn latest_block(&self) -> &Block {
        self.blocks
            .last()
            .expect("blockchain must always contain genesis")
    }

    pub fn block(&self, height: u64) -> Option<&Block> {
        self.blocks.get(height as usize)
    }

    pub fn state(&self) -> &State {
        &self.state
    }

    pub fn state_mut(&mut self) -> &mut State {
        &mut self.state
    }

    pub fn latest_block_hash(&self) -> Hash {
        self.latest_block().hash()
    }

    pub fn execute_transaction(
        &mut self,
        tx: &Transaction,
    ) -> Result<ExecutionReceipt, ChainError> {
        if tx.chain_id != self.chain_id {
            return Err(ChainError::InvalidChainId);
        }

        self.executor
            .execute(&mut self.state, tx)
            .map_err(ChainError::ExecutionFailed)
    }

    pub fn produce_block(
        &mut self,
        timestamp: u64,
        transactions: Vec<Transaction>,
    ) -> Result<Block, ChainError> {
        let expected_height = self
            .height()
            .checked_add(1)
            .ok_or(ChainError::InvalidHeight)?;

        let previous_block = self.latest_block();

        if timestamp < previous_block.header.timestamp {
            return Err(ChainError::InvalidTimestamp);
        }

        let previous_hash = previous_block.hash();

        /*
         * Execute against a working copy.
         *
         * If any transaction fails, self.state remains untouched.
         */
        let mut working_state = self.state.clone();

        for tx in &transactions {
            if tx.chain_id != self.chain_id {
                return Err(ChainError::InvalidChainId);
            }

            self.executor
                .execute(&mut working_state, tx)
                .map_err(ChainError::ExecutionFailed)?;
        }

        let state_root = working_state.state_root();
        let transactions_root = calculate_transactions_root(&transactions);

        let block = Block::new(
            self.chain_id,
            expected_height,
            timestamp,
            previous_hash,
            state_root,
            transactions_root,
            transactions,
        );

        /*
         * Commit only after every transaction and root calculation succeeds.
         */
        self.state = working_state;
        self.blocks.push(block.clone());

        Ok(block)
    }

    pub fn verify_block_link(&self, block: &Block) -> Result<(), ChainError> {
        if block.header.chain_id != self.chain_id {
            return Err(ChainError::InvalidChainId);
        }

        if block.header.height == 0 {
            if !block.is_genesis() {
                return Err(ChainError::InvalidGenesis);
            }

            return Ok(());
        }

        let expected_height = self
            .height()
            .checked_add(1)
            .ok_or(ChainError::InvalidHeight)?;

        if block.header.height != expected_height {
            return Err(ChainError::InvalidHeight);
        }

        if block.header.previous_hash != self.latest_block_hash() {
            return Err(ChainError::InvalidPreviousHash);
        }

        if block.header.timestamp < self.latest_block().header.timestamp {
            return Err(ChainError::InvalidTimestamp);
        }

        Ok(())
    }

    pub fn verify_block_roots(&self, block: &Block) -> bool {
        block.header.transactions_root == block.transactions_root()
            && block.header.state_root == self.state.state_root()
    }
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crypto::Keypair;

    /*
     * IMPORTANT:
     *
     * Alice's address MUST come from the same keypair that signs
     * the transactions. Using [1u8; 20] as a fake address would
     * cause signature/address validation to fail.
     */
    fn addresses() -> (Address, Address, Address) {
        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);

        (alice_keypair.address(), [2u8; 20], [3u8; 20])
    }

    fn transaction(
        chain_id: u64,
        nonce: u64,
        keypair: &Keypair,
        recipient: Address,
        value: u64,
    ) -> Transaction {
        let mut tx = Transaction::new(
            chain_id,
            nonce,
            keypair.address(),
            recipient,
            value,
            21_000,
            1,
            Vec::new(),
        );

        tx.sign(keypair)
            .expect("test transaction should be signable");

        tx
    }

    fn create_chain() -> Blockchain {
        let (alice, bob, fee_recipient) = addresses();

        let mut state = State::new();

        state.create_account(alice, 1_000_000);
        state.create_account(bob, 0);
        state.create_account(fee_recipient, 0);

        Blockchain::new(1, 1_700_000_000, state, fee_recipient)
    }

    #[test]
    fn blockchain_starts_with_genesis() {
        let chain = create_chain();

        assert_eq!(chain.chain_id(), 1);
        assert_eq!(chain.height(), 0);
        assert_eq!(chain.block_count(), 1);
        assert!(chain.latest_block().is_genesis());
    }

    #[test]
    fn transaction_can_be_executed() {
        let mut chain = create_chain();

        let (_, bob, _) = addresses();
        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);

        let tx = transaction(1, 0, &alice_keypair, bob, 10_000);

        let receipt = chain
            .execute_transaction(&tx)
            .expect("transaction should execute");

        assert_eq!(receipt.transaction_hash, tx.hash());
        assert_eq!(
            chain
                .state()
                .get_account(&alice_keypair.address())
                .unwrap()
                .balance,
            969_000
        );
        assert_eq!(chain.state().get_account(&bob).unwrap().balance, 10_000);
    }

    #[test]
    fn block_can_be_produced() {
        let mut chain = create_chain();

        let (_, bob, _) = addresses();
        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);

        let tx = transaction(1, 0, &alice_keypair, bob, 10_000);

        let block = chain
            .produce_block(1_700_000_100, vec![tx])
            .expect("block should be produced");

        assert_eq!(block.header.height, 1);
        assert_eq!(chain.height(), 1);
        assert_eq!(chain.block_count(), 2);
    }

    #[test]
    fn produced_block_links_to_previous_block() {
        let mut chain = create_chain();

        let (_, bob, _) = addresses();
        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);

        let tx = transaction(1, 0, &alice_keypair, bob, 10_000);

        let block = chain
            .produce_block(1_700_000_100, vec![tx])
            .expect("block should be produced");

        assert_eq!(block.header.previous_hash, chain.block(0).unwrap().hash());
    }

    #[test]
    fn wrong_chain_transaction_is_rejected() {
        let mut chain = create_chain();

        let (_, bob, _) = addresses();
        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);

        let tx = transaction(999, 0, &alice_keypair, bob, 10_000);

        let result = chain.execute_transaction(&tx);

        assert_eq!(result, Err(ChainError::InvalidChainId));
    }

    #[test]
    fn wrong_previous_hash_is_rejected() {
        let chain = create_chain();

        let block = Block::new(
            1,
            1,
            1_700_000_100,
            [9u8; 32],
            zero_hash(),
            zero_hash(),
            Vec::new(),
        );

        assert_eq!(
            chain.verify_block_link(&block),
            Err(ChainError::InvalidPreviousHash)
        );
    }

    #[test]
    fn decreasing_timestamp_is_rejected() {
        let mut chain = create_chain();

        let result = chain.produce_block(1_699_999_999, Vec::new());

        assert_eq!(result, Err(ChainError::InvalidTimestamp));
    }

    #[test]
    fn failed_block_does_not_modify_state() {
        let mut chain = create_chain();

        let (_, bob, _) = addresses();
        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);

        let before_root = chain.state().state_root();
        let before_height = chain.height();

        let tx1 = transaction(1, 0, &alice_keypair, bob, 10_000);

        let tx2 = transaction(1, 5, &alice_keypair, bob, 10_000);

        let result = chain.produce_block(1_700_000_100, vec![tx1, tx2]);

        assert!(matches!(
            result,
            Err(ChainError::ExecutionFailed(ExecutionError::InvalidNonce))
        ));

        assert_eq!(chain.state().state_root(), before_root);

        assert_eq!(chain.height(), before_height);
    }

    #[test]
    fn block_can_execute_multiple_sequential_transactions() {
        let mut chain = create_chain();

        let (_, bob, _) = addresses();
        let alice_keypair = Keypair::from_bytes(&[1u8; 32]);

        let tx1 = transaction(1, 0, &alice_keypair, bob, 10_000);

        let tx2 = transaction(1, 1, &alice_keypair, bob, 20_000);

        let block = chain
            .produce_block(1_700_000_100, vec![tx1, tx2])
            .expect("block should be produced");

        assert_eq!(block.transaction_count(), 2);

        assert_eq!(
            chain
                .state()
                .get_account(&alice_keypair.address())
                .unwrap()
                .nonce,
            2
        );

        assert_eq!(chain.state().get_account(&bob).unwrap().balance, 30_000);
    }
}
