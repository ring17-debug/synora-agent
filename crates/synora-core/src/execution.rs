use crate::hash::Hash;
use crate::state::{Address, State};
use crate::transaction::Transaction;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecutionError {
    SenderNotFound,
    InsufficientBalance,
    TransferFailed,
    InvalidChainId,
    InvalidNonce,
    FeeRecipientNotFound,
    InvalidTransaction,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionReceipt {
    pub transaction_hash: Hash,
    pub success: bool,
    pub gas_used: u64,
    pub fee_paid: u128,
    pub value_transferred: u128,
}

pub struct Executor {
    chain_id: u64,
    fee_recipient: Address,
}

impl Executor {
    pub fn new(chain_id: u64, fee_recipient: Address) -> Self {
        Self {
            chain_id,
            fee_recipient,
        }
    }

    pub fn chain_id(&self) -> u64 {
        self.chain_id
    }

    pub fn fee_recipient(&self) -> Address {
        self.fee_recipient
    }

    pub fn execute(
        &self,
        state: &mut State,
        tx: &Transaction,
    ) -> Result<ExecutionReceipt, ExecutionError> {
        if tx.chain_id != self.chain_id {
            return Err(ExecutionError::InvalidChainId);
        }

        tx.validate()
            .map_err(|_| ExecutionError::InvalidTransaction)?;

        let sender = state
            .get_account(&tx.sender)
            .ok_or(ExecutionError::SenderNotFound)?;

        if sender.nonce != tx.nonce {
            return Err(ExecutionError::InvalidNonce);
        }

        if state.get_account(&tx.recipient).is_none() {
            return Err(ExecutionError::TransferFailed);
        }

        if state.get_account(&self.fee_recipient).is_none() {
            return Err(ExecutionError::FeeRecipientNotFound);
        }

        let value = u128::from(tx.value);

        let fee_u64 = tx
            .checked_total_fee()
            .ok_or(ExecutionError::InvalidTransaction)?;

        let fee = u128::from(fee_u64);

        let total_cost = value
            .checked_add(fee)
            .ok_or(ExecutionError::InsufficientBalance)?;

        if sender.balance < total_cost {
            return Err(ExecutionError::InsufficientBalance);
        }

        /*
         * Execute against a temporary state so that the transaction itself
         * is atomic. If any operation fails, the caller's state is untouched.
         */
        let mut working_state = state.clone();

        working_state
            .transfer(tx.sender, tx.recipient, value)
            .map_err(|_| ExecutionError::TransferFailed)?;

        working_state
            .transfer_without_nonce(tx.sender, self.fee_recipient, fee)
            .map_err(|_| ExecutionError::TransferFailed)?;

        *state = working_state;

        Ok(ExecutionReceipt {
            transaction_hash: tx.hash(),
            success: true,
            gas_used: tx.gas_limit,
            fee_paid: fee,
            value_transferred: value,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_transaction(
        chain_id: u64,
        nonce: u64,
        sender: Address,
        recipient: Address,
        value: u64,
    ) -> Transaction {
        Transaction::new(
            chain_id,
            nonce,
            sender,
            recipient,
            value,
            21_000,
            1,
            Vec::new(),
        )
    }

    fn create_executor(fee_recipient: Address) -> Executor {
        Executor::new(1, fee_recipient)
    }

    #[test]
    fn executor_can_execute_transaction() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = State::new();

        state.create_account(alice, 100_000);
        state.create_account(bob, 0);
        state.create_account(fee_recipient, 0);

        let tx = create_transaction(1, 0, alice, bob, 10_000);

        let executor = create_executor(fee_recipient);

        let receipt = executor
            .execute(&mut state, &tx)
            .expect("transaction should execute");

        assert!(receipt.success);
        assert_eq!(receipt.gas_used, 21_000);
        assert_eq!(receipt.fee_paid, 21_000);
        assert_eq!(receipt.value_transferred, 10_000);
        assert_eq!(receipt.transaction_hash, tx.hash());

        assert_eq!(state.get_account(&alice).unwrap().balance, 69_000);
        assert_eq!(state.get_account(&alice).unwrap().nonce, 1);
        assert_eq!(state.get_account(&bob).unwrap().balance, 10_000);
        assert_eq!(state.get_account(&fee_recipient).unwrap().balance, 21_000);
    }

    #[test]
    fn second_transaction_uses_nonce_one() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = State::new();

        state.create_account(alice, 200_000);
        state.create_account(bob, 0);
        state.create_account(fee_recipient, 0);

        let executor = create_executor(fee_recipient);

        let tx1 = create_transaction(1, 0, alice, bob, 10_000);

        executor
            .execute(&mut state, &tx1)
            .expect("first transaction should execute");

        let tx2 = create_transaction(1, 1, alice, bob, 20_000);

        executor
            .execute(&mut state, &tx2)
            .expect("second transaction should execute");

        assert_eq!(state.get_account(&alice).unwrap().nonce, 2);
        assert_eq!(state.get_account(&alice).unwrap().balance, 128_000);
        assert_eq!(state.get_account(&bob).unwrap().balance, 30_000);
        assert_eq!(state.get_account(&fee_recipient).unwrap().balance, 42_000);
    }

    #[test]
    fn insufficient_balance_does_not_modify_state() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = State::new();

        state.create_account(alice, 100);
        state.create_account(bob, 0);
        state.create_account(fee_recipient, 0);

        let before = state.state_root();

        let tx = create_transaction(1, 0, alice, bob, 10_000);

        let executor = create_executor(fee_recipient);

        let result = executor.execute(&mut state, &tx);

        assert_eq!(result, Err(ExecutionError::InsufficientBalance));
        assert_eq!(state.state_root(), before);
    }

    #[test]
    fn wrong_chain_id_is_rejected() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = State::new();

        state.create_account(alice, 100_000);
        state.create_account(bob, 0);
        state.create_account(fee_recipient, 0);

        let tx = create_transaction(2, 0, alice, bob, 10_000);

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::InvalidChainId)
        );
    }

    #[test]
    fn wrong_nonce_is_rejected() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = State::new();

        state.create_account(alice, 100_000);
        state.create_account(bob, 0);
        state.create_account(fee_recipient, 0);

        let tx = create_transaction(1, 5, alice, bob, 10_000);

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::InvalidNonce)
        );
    }

    #[test]
    fn missing_recipient_is_rejected() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = State::new();

        state.create_account(alice, 100_000);
        state.create_account(fee_recipient, 0);

        let tx = create_transaction(1, 0, alice, bob, 10_000);

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::TransferFailed)
        );
    }

    #[test]
    fn missing_fee_recipient_is_rejected() {
        let alice = [1u8; 20];
        let bob = [2u8; 20];
        let fee_recipient = [3u8; 20];

        let mut state = State::new();

        state.create_account(alice, 100_000);
        state.create_account(bob, 0);

        let tx = create_transaction(1, 0, alice, bob, 10_000);

        let executor = create_executor(fee_recipient);

        assert_eq!(
            executor.execute(&mut state, &tx),
            Err(ExecutionError::FeeRecipientNotFound)
        );
    }
}
