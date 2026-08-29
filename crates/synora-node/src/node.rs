use std::time::{SystemTime, UNIX_EPOCH};

use synora_core::{
    block::Block,
    chain::{Blockchain, ChainError},
    consensus::{
        BlockProposal, CommitDecision, ConsensusEngine, ConsensusError, ConsensusPhase,
        ConsensusRound, ValidatorId,
    },
    mempool::{Mempool, MempoolError},
    state::{Address, State},
    transaction::Transaction,
};

use crate::config::NodeConfig;

#[derive(Debug, PartialEq, Eq)]
pub enum NodeError {
    Mempool(MempoolError),
    Chain(ChainError),
    Consensus(ConsensusError),
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

impl From<ConsensusError> for NodeError {
    fn from(error: ConsensusError) -> Self {
        Self::Consensus(error)
    }
}

pub struct SynoraNode {
    config: NodeConfig,
    chain: Blockchain,
    mempool: Mempool,
    consensus: ConsensusEngine,
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

        /*
         * Genesis is height 0.
         *
         * The first consensus round therefore starts at height 1,
         * which is the first block that validators need to agree on.
         */
        let consensus = ConsensusEngine::new(
            config.validator_set.clone(),
            chain.height().saturating_add(1),
        );

        Self {
            config,
            chain,
            mempool,
            consensus,
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

    // ---------------------------------------------------------------------
    // Consensus accessors
    // ---------------------------------------------------------------------

    /// Returns the validator set used by this node.
    pub fn validator_set(&self) -> &synora_core::consensus::ValidatorSet {
        self.consensus.validator_set()
    }

    /// Returns this node's validator identity.
    pub fn validator_id(&self) -> ValidatorId {
        self.config.validator_id
    }

    /// Returns the current consensus round.
    pub fn consensus_round(&self) -> ConsensusRound {
        self.consensus.round()
    }

    /// Returns the current consensus phase.
    pub fn consensus_phase(&self) -> ConsensusPhase {
        self.consensus.phase()
    }

    /// Returns the current consensus proposal.
    pub fn consensus_proposal(&self) -> Option<&BlockProposal> {
        self.consensus.proposal()
    }

    /// Returns the commit decision if consensus has finalized a block.
    pub fn consensus_decision(&self) -> Option<CommitDecision> {
        self.consensus.decision()
    }

    /// Returns whether consensus has committed the current height.
    pub fn consensus_is_committed(&self) -> bool {
        self.consensus.is_committed()
    }

    /// Returns a reference to the underlying consensus engine.
    pub fn consensus(&self) -> &ConsensusEngine {
        &self.consensus
    }

    /// Returns a mutable reference to the underlying consensus engine.
    pub fn consensus_mut(&mut self) -> &mut ConsensusEngine {
        &mut self.consensus
    }

    // ---------------------------------------------------------------------
    // Transactions / mempool
    // ---------------------------------------------------------------------

    pub fn submit_transaction(&mut self, tx: Transaction) -> Result<(), NodeError> {
        let state = self.chain.state();

        self.mempool.submit(state, tx)?;

        Ok(())
    }

    pub fn pending_transactions(&self) -> usize {
        self.mempool.len()
    }

    // ---------------------------------------------------------------------
    // Block production
    // ---------------------------------------------------------------------

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

        /*
         * The block has now been committed to the local chain.
         *
         * The consensus engine represents the consensus state for the
         * next block height, so reset it after local block production.
         */
        self.reset_consensus_for_next_height();

        Ok(block)
    }

    /// Creates a proposal for the current consensus height.
    ///
    /// The block itself is not committed to the chain. This method only
    /// submits proposal metadata into the consensus state machine.
    pub fn submit_consensus_proposal(
        &mut self,
        block: &Block,
        round: u64,
    ) -> Result<(), NodeError> {
        let proposal = BlockProposal::new(block, round, self.config.validator_id);

        self.consensus.submit_proposal(proposal)?;

        Ok(())
    }

    /// Submit this node's prevote for the current proposal.
    pub fn submit_own_prevote(&mut self) -> Result<ConsensusPhase, NodeError> {
        Ok(self.consensus.submit_prevote(self.config.validator_id)?)
    }

    /// Submit this node's precommit for the current proposal.
    pub fn submit_own_precommit(&mut self) -> Result<ConsensusPhase, NodeError> {
        Ok(self.consensus.submit_precommit(self.config.validator_id)?)
    }

    /// Submit another validator's prevote.
    ///
    /// Networking/signature verification will eventually live above this
    /// layer. At this stage the deterministic consensus engine validates the
    /// validator identity and vote consistency.
    pub fn submit_prevote(&mut self, validator: ValidatorId) -> Result<ConsensusPhase, NodeError> {
        Ok(self.consensus.submit_prevote(validator)?)
    }

    /// Submit another validator's precommit.
    pub fn submit_precommit(
        &mut self,
        validator: ValidatorId,
    ) -> Result<ConsensusPhase, NodeError> {
        Ok(self.consensus.submit_precommit(validator)?)
    }

    /// Advance the consensus engine to the next round.
    pub fn advance_consensus_round(&mut self) -> Result<ConsensusRound, NodeError> {
        Ok(self.consensus.advance_round()?)
    }

    fn reset_consensus_for_next_height(&mut self) {
        let next_height = self.chain.height().saturating_add(1);

        self.consensus = ConsensusEngine::new(self.config.validator_set.clone(), next_height);
    }

    // ---------------------------------------------------------------------
    // Accounts / transactions
    // ---------------------------------------------------------------------

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

        assert_eq!(node.validator_set().len(), 3);
        assert_eq!(node.validator_set().total_power(), 3);
        assert_eq!(node.validator_id(), [1u8; 20]);

        assert_eq!(node.consensus_round(), ConsensusRound::new(1, 0));

        assert_eq!(node.consensus_phase(), ConsensusPhase::Propose);
        assert!(!node.consensus_is_committed());
    }

    #[test]
    fn consensus_engine_is_initialized_for_next_block_height() {
        let config = NodeConfig::devnet();

        let node = SynoraNode::new(config, 1_700_000_000);

        assert_eq!(node.chain().height(), 0);
        assert_eq!(node.consensus_round().height, 1);
        assert_eq!(node.consensus_round().round, 0);
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

        /*
         * Local block production advances the consensus height.
         */
        assert_eq!(node.consensus_round(), ConsensusRound::new(2, 0));
        assert_eq!(node.consensus_phase(), ConsensusPhase::Propose);
    }

    #[test]
    fn consensus_proposal_is_accepted_for_current_height() {
        let config = NodeConfig::devnet();
        let mut node = SynoraNode::new(config, 1_700_000_000);

        /*
         * The chain starts with genesis at height 0.
         * The first consensus height is therefore 1.
         *
         * With three validators and the current round-robin proposer
         * formula, validator 2 is the proposer for height 1, round 0:
         *
         *     (1 + 0) % 3 = 1
         */
        let block = Block::new(
            1337,
            1,
            1_700_000_100,
            [0u8; 32],
            [0u8; 32],
            [0u8; 32],
            Vec::new(),
        );

        /*
         * Validator 2 is the deterministic proposer for height 1,
         * round 0.
         */
        let proposal = BlockProposal::new(&block, 0, [2u8; 20]);

        node.consensus_mut()
            .submit_proposal(proposal)
            .expect("proposal should be accepted");

        assert_eq!(node.consensus_phase(), ConsensusPhase::Prevote);
        assert_eq!(node.consensus_proposal().unwrap().proposer, [2u8; 20]);
        assert_eq!(node.consensus_proposal().unwrap().height, 1);
    }

    #[test]
    fn consensus_proposal_must_match_engine_height() {
        let config = NodeConfig::devnet();

        let mut node = SynoraNode::new(config, 1_700_000_000);

        /*
         * Genesis is height 0 while the consensus engine expects height 1.
         * This test intentionally verifies that mismatch is rejected.
         */
        let block = Block::genesis(1337, 1_700_000_100);

        let result = node.submit_consensus_proposal(&block, 0);

        assert_eq!(
            result,
            Err(NodeError::Consensus(ConsensusError::InvalidProposalHeight))
        );
    }

    #[test]
    fn own_prevote_requires_proposal() {
        let config = NodeConfig::devnet();

        let mut node = SynoraNode::new(config, 1_700_000_000);

        assert_eq!(
            node.submit_own_prevote(),
            Err(NodeError::Consensus(ConsensusError::ProposalRequired))
        );
    }

    #[test]
    fn consensus_can_reach_precommit_phase() {
        let config = NodeConfig::devnet();

        let mut node = SynoraNode::new(config, 1_700_000_000);

        /*
         * Build a proposal for height 1.
         *
         * Validator 2 is the deterministic proposer for height 1,
         * round 0 under the current round-robin formula:
         *
         *     (1 + 0) % 3 = 1
         */
        let proposal_block = Block::new(
            1337,
            1,
            1_700_000_100,
            [0u8; 32],
            [0u8; 32],
            [0u8; 32],
            Vec::new(),
        );

        /*
         * The engine requires validator 2 to be the proposer, so use
         * the proposal metadata directly here.
         */
        let proposal = BlockProposal::new(&proposal_block, 0, [2u8; 20]);

        node.consensus_mut()
            .submit_proposal(proposal)
            .expect("proposal should be accepted");

        assert_eq!(node.consensus_phase(), ConsensusPhase::Prevote);

        node.submit_prevote([1u8; 20])
            .expect("first prevote should be accepted");

        assert_eq!(
            node.submit_prevote([2u8; 20])
                .expect("second prevote should reach quorum"),
            ConsensusPhase::Precommit
        );

        assert_eq!(node.consensus_phase(), ConsensusPhase::Precommit);
    }

    #[test]
    fn consensus_can_commit_block() {
        let config = NodeConfig::devnet();

        let mut node = SynoraNode::new(config, 1_700_000_000);

        /*
         * The first consensus height is 1, therefore validator 2
         * is the proposer at round 0.
         */
        let block = Block::new(
            1337,
            1,
            1_700_000_100,
            [0u8; 32],
            [0u8; 32],
            [0u8; 32],
            Vec::new(),
        );

        let proposal = BlockProposal::new(&block, 0, [2u8; 20]);

        node.consensus_mut()
            .submit_proposal(proposal.clone())
            .expect("proposal should be accepted");

        node.submit_prevote([1u8; 20])
            .expect("first prevote should be accepted");

        node.submit_prevote([2u8; 20])
            .expect("second prevote should reach quorum");

        assert_eq!(node.consensus_phase(), ConsensusPhase::Precommit);

        node.submit_precommit([1u8; 20])
            .expect("first precommit should be accepted");

        node.submit_precommit([2u8; 20])
            .expect("second precommit should commit");

        assert_eq!(node.consensus_phase(), ConsensusPhase::Committed);

        assert_eq!(
            node.consensus_decision(),
            Some(CommitDecision::new(1, 0, proposal.block_hash))
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
