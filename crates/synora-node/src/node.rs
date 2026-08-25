use std::time::{SystemTime, UNIX_EPOCH};

use synora_core::{
    block::Block,
    chain::{Blockchain, ChainError},
    mempool::{Mempool, MempoolError},
    state::{Address, State},
    transaction::Transaction,
};

use crate::config::NodeConfig;

#[derive(Debug, PartialEq, Eq)]
pub enum NodeError {
    Mempool(MempoolError),
    Chain(ChainError),
    NoTransactions,
    BlockGasLimitExceeded,
}

impl From<MempoolError> for NodeError {
    fn from(error: MempoolError) -> Self {
        Self::Mempool(error)
    }
}

impl From<ChainError> for NodeError {
    fn from(error: ChainError) -> Self {
        Self::Chain(error)
    }
}

pub struct SynoraNode {
    config: NodeConfig,
    chain: Blockchain,
    mempool: Mempool,
}

#[allow(dead_code)]
impl SynoraNode {
    pub fn new(config: NodeConfig, genesis_timestamp: u64) -> Self {
        let state = State::new();

        let chain = Blockchain::new(
            config.chain_id,
            genesis_timestamp,
            state,
            config.fee_recipient,
        );

        let mempool = Mempool::new(config.chain_id, config.mempool_capacity);

        Self {
            config,
            chain,
            mempool,
        }
    }

    pub fn config(&self) -> &NodeConfig {
        &self.config
    }

    pub fn chain_id(&self) -> u64 {
        self.config.chain_id
    }

    pub fn fee_recipient(&self) -> Address {
        self.config.fee_recipient
    }

    pub fn block_gas_limit(&self) -> u64 {
        self.config.block_gas_limit
    }

    pub fn chain(&self) -> &Blockchain {
        &self.chain
    }

    pub fn chain_mut(&mut self) -> &mut Blockchain {
        &mut self.chain
    }

    pub fn mempool(&self) -> &Mempool {
        &self.mempool
    }

    pub fn state(&self) -> &State {
        self.chain.state()
    }

    pub fn submit_transaction(&mut self, tx: Transaction) -> Result<(), NodeError> {
        let state = self.chain.state();

        self.mempool.submit(state, tx)?;

        Ok(())
    }

    pub fn pending_transactions(&self) -> usize {
        self.mempool.len()
    }

    pub fn produce_block(&mut self, timestamp: Option<u64>) -> Result<Block, NodeError> {
        if self.mempool.is_empty() {
            return Err(NodeError::NoTransactions);
        }

        let transactions = self.select_block_transactions();

        if transactions.is_empty() {
            return Err(NodeError::BlockGasLimitExceeded);
        }

        let timestamp = timestamp.unwrap_or_else(current_timestamp);

        let block = self.chain.produce_block(timestamp, transactions)?;

        for transaction in &block.transactions {
            self.mempool.remove(&transaction.hash());
        }

        Ok(block)
    }

    pub fn create_account(&mut self, address: Address, balance: u128) {
        self.chain.state_mut().create_account(address, balance);
    }

    /// Find a transaction in the mempool or confirmed blocks.
    ///
    /// Returns:
    /// - `Some((None, tx))` when the transaction is pending.
    /// - `Some((Some(height), tx))` when the transaction is confirmed.
    /// - `None` when the transaction does not exist.
    pub fn find_transaction(
        &self,
        hash: &synora_core::hash::Hash,
    ) -> Option<(Option<u64>, &Transaction)> {
        if let Some(transaction) = self.mempool.get(hash) {
            return Some((None, transaction));
        }

        for height in 1..=self.chain.height() {
            let Some(block) = self.chain.block(height) else {
                continue;
            };

            if let Some(transaction) = block
                .transactions
                .iter()
                .find(|transaction| transaction.hash() == *hash)
            {
                return Some((Some(height), transaction));
            }
        }

        None
    }

    fn select_block_transactions(&self) -> Vec<Transaction> {
        let gas_limit = self.config.block_gas_limit;

        let mut total_gas = 0u64;
        let mut selected = Vec::new();

        for transaction in self.mempool.transactions() {
            let gas = transaction.gas_limit;

            if gas > gas_limit {
                continue;
            }

            if total_gas.saturating_add(gas) > gas_limit {
                continue;
            }

            total_gas = total_gas.saturating_add(gas);
            selected.push(transaction.clone());
        }

        selected
    }
}

fn current_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use super::*;
    use synora_core::crypto::Keypair;

    struct TestAccount {
        keypair: Keypair,
        address: Address,
    }

    impl TestAccount {
        fn new(seed: u8) -> Self {
            let keypair = Keypair::from_bytes(&[seed; 32]);

            let address = keypair.address();

            Self { keypair, address }
        }
    }

    fn transaction(
        chain_id: u64,
        keypair: &Keypair,
        recipient: Address,
        nonce: u64,
        gas_limit: u64,
    ) -> Transaction {
        let mut tx = Transaction::new(
            chain_id,
            nonce,
            keypair.address(),
            recipient,
            1_000,
            gas_limit,
            1,
            Vec::new(),
        );

        tx.sign(keypair)
            .expect("test transaction should be signable");

        tx
    }

    fn setup_node(block_gas_limit: u64) -> (SynoraNode, TestAccount, TestAccount, Address) {
        let config = NodeConfig::new(1337, [0xFE; 20], 100, block_gas_limit);

        let mut node = SynoraNode::new(config, 1_700_000_000);

        let alice = TestAccount::new(1);
        let carol = TestAccount::new(3);
        let bob = [2u8; 20];
        let fee_recipient = [0xFE; 20];

        node.create_account(alice.address, 1_000_000);
        node.create_account(carol.address, 1_000_000);
        node.create_account(bob, 0);
        node.create_account(fee_recipient, 0);

        (node, alice, carol, bob)
    }

    #[test]
    fn node_starts_with_genesis() {
        let config = NodeConfig::devnet();

        let node = SynoraNode::new(config, 1_700_000_000);

        assert_eq!(node.chain_id(), 1337);
        assert_eq!(node.chain().height(), 0);
        assert_eq!(node.pending_transactions(), 0);
    }

    #[test]
    fn transaction_can_be_submitted() {
        let (mut node, alice, _, bob) = setup_node(1_000_000);

        node.submit_transaction(transaction(1337, &alice.keypair, bob, 0, 21_000))
            .expect("transaction should enter mempool");

        assert_eq!(node.pending_transactions(), 1);
    }

    #[test]
    fn block_can_be_produced_from_mempool() {
        let (mut node, alice, _, bob) = setup_node(1_000_000);

        node.submit_transaction(transaction(1337, &alice.keypair, bob, 0, 21_000))
            .expect("transaction should enter mempool");

        let block = node
            .produce_block(Some(1_700_000_100))
            .expect("block should be produced");

        assert_eq!(block.header.height, 1);
        assert_eq!(block.transaction_count(), 1);
        assert_eq!(node.pending_transactions(), 0);

        assert_eq!(
            node.state().get_account(&alice.address).unwrap().balance,
            978_000
        );

        assert_eq!(node.state().get_account(&alice.address).unwrap().nonce, 1);

        assert_eq!(node.state().get_account(&bob).unwrap().balance, 1_000);

        assert_eq!(
            node.state().get_account(&[0xFE; 20]).unwrap().balance,
            21_000
        );
    }

    #[test]
    fn block_respects_gas_limit() {
        let (mut node, alice, carol, bob) = setup_node(42_000);

        /*
         * Two different senders are used because the current mempool
         * requires each sender's transaction nonce to equal its
         * current state nonce.
         */
        node.submit_transaction(transaction(1337, &alice.keypair, bob, 0, 21_000))
            .expect("Alice transaction should enter mempool");

        node.submit_transaction(transaction(1337, &carol.keypair, bob, 0, 21_000))
            .expect("Carol transaction should enter mempool");

        let block = node
            .produce_block(Some(1_700_000_100))
            .expect("block should be produced");

        assert_eq!(block.transaction_count(), 2);
        assert_eq!(block.transactions[0].gas_limit, 21_000);
        assert_eq!(block.transactions[1].gas_limit, 21_000);
        assert_eq!(node.pending_transactions(), 0);
    }

    #[test]
    fn transaction_that_does_not_fit_stays_in_mempool() {
        let (mut node, alice, carol, bob) = setup_node(21_000);

        node.submit_transaction(transaction(1337, &alice.keypair, bob, 0, 21_000))
            .expect("Alice transaction should enter mempool");

        node.submit_transaction(transaction(1337, &carol.keypair, bob, 0, 21_000))
            .expect("Carol transaction should enter mempool");

        let block = node
            .produce_block(Some(1_700_000_100))
            .expect("block should be produced");

        assert_eq!(block.transaction_count(), 1);
        assert_eq!(node.pending_transactions(), 1);
    }

    #[test]
    fn oversized_transaction_is_not_selected() {
        let (mut node, alice, _, bob) = setup_node(20_000);

        node.submit_transaction(transaction(1337, &alice.keypair, bob, 0, 21_000))
            .expect("transaction should enter mempool");

        let result = node.produce_block(Some(1_700_000_100));

        assert_eq!(result.unwrap_err(), NodeError::BlockGasLimitExceeded);

        assert_eq!(node.pending_transactions(), 1);
        assert_eq!(node.chain().height(), 0);
    }
}
